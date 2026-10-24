package sentinel;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;

/**
 * Streaming per-trader-window feature extractor: a port of sentinel.features.extractor (Python).
 * Every feature is computed with the same operations in the same order as the Python reference.
 */
public final class FeatureExtractor {
    public static final String[] FEATURE_NAMES = {
        "n_new", "n_cancel", "n_modify", "n_fills", "order_to_trade_ratio",
        "cancel_rate", "cancel_within_1s_share", "cancel_within_3s_share", "median_cancelled_lifetime_s",
        "total_new_qty", "total_fill_qty", "max_order_qty", "max_qty_over_best", "peak_resting_over_depth5",
        "max_live_levels_same_side", "max_cancel_burst_100ms",
        "opposite_fill_share", "signed_mid_drift_ticks",
        "net_over_gross", "top_counterparty_share", "n_counterparties", "roundtrip_counterparty_share",
    };
    public static final int N_FEATURES = FEATURE_NAMES.length;

    private static final long NS_PER_SEC = 1_000_000_000L;
    private static final long BURST_NS = 100_000_000L;
    private static final int NEW = 0;
    private static final int CANCEL = 1;
    private static final int MODIFY = 2;
    private static final int EXEC = 3;
    private static final int BID = 1;

    /** Features of one trader in one closed window. */
    public static final class Row {
        public final long trader;
        public final int instrument;
        public final long window;
        public final double[] values;

        Row(long trader, int instrument, long window, double[] values) {
            this.trader = trader;
            this.instrument = instrument;
            this.window = window;
            this.values = values;
        }
    }

    private static final class Live {
        final long ts;
        final int side;
        long price;
        long qty;

        Live(long ts, int side, long price, long qty) {
            this.ts = ts;
            this.side = side;
            this.price = price;
            this.qty = qty;
        }
    }

    private static final class Win {
        long nNew;
        long nNewLimit;
        long nCancel;
        long nModify;
        long nFills;
        long newQty;
        long fillBuy;
        long fillSell;
        long maxQty;
        long quick1;
        long quick3;
        long maxLevels;
        long maxBurst;
        long peakBid;
        long peakAsk;
        double maxQtyOverBest;
        double peakRatio;
        double firstMid;
        double lastMid;
        boolean hasMid;
        double[] lifetimes = new double[8];
        int nLifetimes;
        final HashMap<Long, Long> cpBuy = new HashMap<>();
        final HashMap<Long, Long> cpSell = new HashMap<>();

        void addLifetime(double v) {
            if (nLifetimes == lifetimes.length) {
                lifetimes = Arrays.copyOf(lifetimes, nLifetimes * 2);
            }
            lifetimes[nLifetimes++] = v;
        }
    }

    private static final class Trader {
        final HashMap<Long, Live> live = new HashMap<>();
        long restingBid;
        long restingAsk;
        final HashMap<Long, Integer> levelsBid = new HashMap<>();
        final HashMap<Long, Integer> levelsAsk = new HashMap<>();
        final ArrayDeque<Long> cancels = new ArrayDeque<>();
        Win win;

        HashMap<Long, Integer> levels(int side) {
            return side == BID ? levelsBid : levelsAsk;
        }

        long resting(int side) {
            return side == BID ? restingBid : restingAsk;
        }

        void addResting(int side, long delta) {
            if (side == BID) {
                restingBid += delta;
            } else {
                restingAsk += delta;
            }
        }
    }

    private static final class Instrument {
        boolean hasWindow;
        long window;
        long lastTs = -1;
        final HashMap<Long, Trader> traders = new HashMap<>();
        final ArrayList<Long> active = new ArrayList<>();
    }

    private static final List<Row> NONE = Collections.emptyList();

    private final long windowNs;
    private final Instrument[] instruments;

    public FeatureExtractor(int windowS, int nInstruments) {
        this.windowNs = windowS * NS_PER_SEC;
        this.instruments = new Instrument[nInstruments];
        for (int i = 0; i < nInstruments; i++) {
            instruments[i] = new Instrument();
        }
    }

    /** Process one event; returns the rows of any window this event closed (usually empty). */
    public List<Row> process(long ts, int instrument, int etype, long orderId, long traderId, long counterparty,
                             int side, long price, long qty, long bestBid, long bestAsk, long bidQty1,
                             long askQty1, long bidDepth5, long askDepth5) {
        Instrument inst = instruments[instrument];
        if (ts < inst.lastTs) {
            throw new IllegalStateException("out-of-order event: " + ts + " < " + inst.lastTs);
        }
        inst.lastTs = ts;
        long window = ts / windowNs;
        List<Row> closed = NONE;
        if (!inst.hasWindow) {
            inst.window = window;
            inst.hasWindow = true;
        } else if (window > inst.window) {
            closed = close(inst, instrument);
            inst.window = window;
        }

        Trader t = inst.traders.get(traderId);
        if (t == null) {
            t = new Trader();
            inst.traders.put(traderId, t);
        }
        Win w = t.win;
        if (w == null) {
            w = new Win();
            t.win = w;
            inst.active.add(traderId);
        }
        if (bestBid != 0 && bestAsk != 0) {
            double mid = (bestBid + bestAsk) / 2.0;
            if (!w.hasMid) {
                w.firstMid = mid;
                w.hasMid = true;
            }
            w.lastMid = mid;
        }

        long ownBest = side == BID ? bidQty1 : askQty1;
        long ownDepth = side == BID ? bidDepth5 : askDepth5;
        switch (etype) {
            case NEW -> onNew(t, w, ts, orderId, side, price, qty, ownBest, ownDepth);
            case CANCEL -> onCancel(t, w, ts, orderId);
            case MODIFY -> onModify(t, w, orderId, price, qty, ownDepth);
            case EXEC -> onExec(t, w, orderId, side, qty, counterparty);
            default -> throw new IllegalArgumentException("bad event type " + etype);
        }
        return closed;
    }

    /** Close every open window (end of stream). */
    public List<Row> flush() {
        List<Row> rows = new ArrayList<>();
        for (int i = 0; i < instruments.length; i++) {
            Instrument inst = instruments[i];
            if (inst.hasWindow && !inst.active.isEmpty()) {
                rows.addAll(close(inst, i));
            }
        }
        return rows;
    }

    private void onNew(Trader t, Win w, long ts, long orderId, int side, long price, long qty,
                       long ownBest, long ownDepth) {
        w.nNew++;
        if (price <= 0) {
            return;
        }
        w.nNewLimit++;
        w.newQty += qty;
        w.maxQty = Math.max(w.maxQty, qty);
        w.maxQtyOverBest = Math.max(w.maxQtyOverBest, (double) qty / Math.max(1L, ownBest));
        t.live.put(orderId, new Live(ts, side, price, qty));
        t.addResting(side, qty);
        levelUp(t, side, price);
        notePeaks(t, w, side, ownDepth);
    }

    private void onCancel(Trader t, Win w, long ts, long orderId) {
        w.nCancel++;
        Live live = removeLive(t, orderId);
        if (live != null) {
            double lifetime = (double) (ts - live.ts) / NS_PER_SEC;
            w.addLifetime(lifetime);
            if (lifetime <= 1.0) {
                w.quick1++;
            }
            if (lifetime <= 3.0) {
                w.quick3++;
            }
        }
        ArrayDeque<Long> recent = t.cancels;
        recent.addLast(ts);
        while (ts - recent.peekFirst() > BURST_NS) {
            recent.pollFirst();
        }
        w.maxBurst = Math.max(w.maxBurst, recent.size());
    }

    private void onModify(Trader t, Win w, long orderId, long price, long qty, long ownDepth) {
        w.nModify++;
        Live live = t.live.get(orderId);
        if (live == null) {
            return;
        }
        t.addResting(live.side, qty - live.qty);
        if (price != live.price) {
            levelDown(t, live.side, live.price);
            levelUp(t, live.side, price);
            live.price = price;
        }
        live.qty = qty;
        notePeaks(t, w, live.side, ownDepth);
    }

    private void onExec(Trader t, Win w, long orderId, int side, long qty, long counterparty) {
        w.nFills++;
        if (side == BID) {
            w.fillBuy += qty;
            w.cpBuy.merge(counterparty, qty, Long::sum);
        } else {
            w.fillSell += qty;
            w.cpSell.merge(counterparty, qty, Long::sum);
        }
        Live live = t.live.get(orderId);
        if (live != null) {
            live.qty -= qty;
            t.addResting(live.side, -qty);
            if (live.qty <= 0) {
                removeLive(t, orderId);
            }
        }
    }

    private static void levelUp(Trader t, int side, long price) {
        t.levels(side).merge(price, 1, Integer::sum);
    }

    private static void levelDown(Trader t, int side, long price) {
        HashMap<Long, Integer> levels = t.levels(side);
        int n = levels.get(price);
        if (n > 1) {
            levels.put(price, n - 1);
        } else {
            levels.remove(price);
        }
    }

    private static Live removeLive(Trader t, long orderId) {
        Live live = t.live.remove(orderId);
        if (live != null) {
            t.addResting(live.side, -live.qty);
            levelDown(t, live.side, live.price);
        }
        return live;
    }

    private static void notePeaks(Trader t, Win w, int side, long ownDepth) {
        long resting = t.resting(side);
        if (side == BID) {
            w.peakBid = Math.max(w.peakBid, resting);
        } else {
            w.peakAsk = Math.max(w.peakAsk, resting);
        }
        w.peakRatio = Math.max(w.peakRatio, (double) resting / Math.max(1L, ownDepth));
        w.maxLevels = Math.max(w.maxLevels, t.levels(side).size());
    }

    private List<Row> close(Instrument inst, int instrument) {
        long[] ids = new long[inst.active.size()];
        for (int i = 0; i < ids.length; i++) {
            ids[i] = inst.active.get(i);
        }
        Arrays.sort(ids);
        List<Row> rows = new ArrayList<>(ids.length);
        for (long id : ids) {
            Trader t = inst.traders.get(id);
            rows.add(new Row(id, instrument, inst.window, features(t.win)));
            t.win = null;
        }
        inst.active.clear();
        return rows;
    }

    private static double[] features(Win w) {
        double limitDen = Math.max(1L, w.nNewLimit);
        long totalFill = w.fillBuy + w.fillSell;
        int peakSide = w.peakBid >= w.peakAsk ? BID : -1;
        boolean hasPeak = (w.peakBid + w.peakAsk) > 0;
        double opposite = 0.0;
        if (hasPeak && totalFill != 0) {
            long oppositeQty = peakSide == BID ? w.fillSell : w.fillBuy;
            opposite = (double) oppositeQty / totalFill;
        }
        double drift = 0.0;
        if (hasPeak && w.hasMid) {
            drift = (peakSide == BID ? 1 : -1) * (w.lastMid - w.firstMid);
        }
        HashMap<Long, Long> totals = new HashMap<>(w.cpBuy);
        w.cpSell.forEach((cp, q) -> totals.merge(cp, q, Long::sum));
        long roundtrip = 0;
        for (Long cp : w.cpBuy.keySet()) {
            if (w.cpSell.containsKey(cp)) {
                roundtrip += totals.get(cp);
            }
        }
        long topCounterparty = 0;
        for (long q : totals.values()) {
            topCounterparty = Math.max(topCounterparty, q);
        }
        double median = -1.0;
        if (w.nLifetimes > 0) {
            double[] sorted = Arrays.copyOf(w.lifetimes, w.nLifetimes);
            Arrays.sort(sorted);
            int mid = sorted.length / 2;
            median = sorted.length % 2 == 1 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
        }
        double[] f = new double[N_FEATURES];
        f[0] = w.nNew;
        f[1] = w.nCancel;
        f[2] = w.nModify;
        f[3] = w.nFills;
        f[4] = (double) w.nNew / Math.max(1L, w.nFills);
        f[5] = (double) w.nCancel / Math.max(1L, w.nNew);
        f[6] = Math.min(1.0, w.quick1 / limitDen);
        f[7] = Math.min(1.0, w.quick3 / limitDen);
        f[8] = median;
        f[9] = w.newQty;
        f[10] = totalFill;
        f[11] = w.maxQty;
        f[12] = w.maxQtyOverBest;
        f[13] = w.peakRatio;
        f[14] = w.maxLevels;
        f[15] = w.maxBurst;
        f[16] = opposite;
        f[17] = drift;
        f[18] = totalFill != 0 ? (double) Math.abs(w.fillBuy - w.fillSell) / totalFill : 1.0;
        f[19] = totalFill != 0 && !totals.isEmpty() ? (double) topCounterparty / totalFill : 0.0;
        f[20] = totals.size();
        f[21] = totalFill != 0 ? (double) roundtrip / totalFill : 0.0;
        return f;
    }
}
