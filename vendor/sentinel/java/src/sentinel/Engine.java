package sentinel;

import java.util.ArrayList;
import java.util.List;

/** Feature extractor plus LightGBM scoring and rules, one event at a time. */
public final class Engine {
    public static final String[] CLASS_NAMES = {"NORMAL", "SPOOFING", "LAYERING", "WASH"};

    /** Receives every scored feature row with its class probabilities (used by the run mode). */
    public interface RowSink {
        void accept(FeatureExtractor.Row row, double[] proba);
    }

    /** A flagged trader-window from one detector. */
    public static final class Alert {
        public final String detector;
        public final String kind;
        public final long trader;
        public final int instrument;
        public final long window;
        public final double score;

        Alert(String detector, String kind, long trader, int instrument, long window, double score) {
            this.detector = detector;
            this.kind = kind;
            this.trader = trader;
            this.instrument = instrument;
            this.window = window;
            this.score = score;
        }
    }

    private final FeatureExtractor extractor;
    private final LgbmModel model;
    private final Config config;
    private final RowSink sink;
    private final double[] proba = new double[4];
    public final List<Alert> alerts = new ArrayList<>();
    public long rowsScored;

    public Engine(Config config, LgbmModel model, RowSink sink) {
        this.config = config;
        this.model = model;
        this.sink = sink;
        this.extractor = new FeatureExtractor(config.windowS, config.nInstruments);
    }

    /** Process one event; returns the number of feature rows scored (non-zero when a window closed). */
    public int process(long ts, int instrument, int etype, long orderId, long traderId, long counterparty,
                       int side, long price, long qty, long bestBid, long bestAsk, long bidQty1,
                       long askQty1, long bidDepth5, long askDepth5) {
        List<FeatureExtractor.Row> closed = extractor.process(ts, instrument, etype, orderId, traderId,
                counterparty, side, price, qty, bestBid, bestAsk, bidQty1, askQty1, bidDepth5, askDepth5);
        if (closed.isEmpty()) {
            return 0;
        }
        score(closed);
        return closed.size();
    }

    /** Close and score every open window (end of stream). */
    public int flush() {
        List<FeatureExtractor.Row> closed = extractor.flush();
        if (!closed.isEmpty()) {
            score(closed);
        }
        return closed.size();
    }

    private void score(List<FeatureExtractor.Row> rows) {
        for (FeatureExtractor.Row row : rows) {
            model.predict(row.values, proba);
            if (sink != null) {
                sink.accept(row, proba);
            }
            int type = 0;
            double best = -1.0;
            for (int k = 1; k < 4; k++) {
                if (proba[k] >= config.thresholds[k] && proba[k] > best) {
                    type = k;
                    best = proba[k];
                }
            }
            if (type != 0) {
                alerts.add(new Alert("model", CLASS_NAMES[type], row.trader, row.instrument, row.window, proba[type]));
            }
            for (Config.Rule rule : config.rules) {
                if (rule.flags(row.values)) {
                    alerts.add(new Alert(rule.detector, rule.kind, row.trader, row.instrument, row.window, 1.0));
                }
            }
        }
        rowsScored += rows.size();
    }
}
