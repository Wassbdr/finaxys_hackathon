package sentinel;

import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.Locale;

/**
 * Command line entry point.
 *   run   events.bin engine.properties model.txt out_dir [n]   writes features.csv and alerts.csv
 *   bench events.bin engine.properties model.txt [n]           prints one JSON line of timings
 */
public final class Main {
    private static final int F = EventReader.FIELDS;

    public static void main(String[] args) throws Exception {
        if (args.length == 0) {
            usage();
        }
        switch (args[0]) {
            case "run" -> run(args);
            case "bench" -> bench(args);
            default -> usage();
        }
    }

    private static void usage() {
        System.err.println("usage: run events.bin engine.properties model.txt out_dir [n] | "
                + "bench events.bin engine.properties model.txt [n]");
        System.exit(2);
    }

    private static Engine feed(EventReader.Events ev, Config cfg, LgbmModel model, Engine.RowSink sink) {
        Engine engine = new Engine(cfg, model, sink);
        long[] d = ev.data;
        for (int i = 0; i < ev.n; i++) {
            int o = i * F;
            engine.process(d[o], (int) d[o + 1], (int) d[o + 2], d[o + 3], d[o + 4], d[o + 5], (int) d[o + 6],
                    d[o + 7], d[o + 8], d[o + 9], d[o + 10], d[o + 11], d[o + 12], d[o + 13], d[o + 14]);
        }
        engine.flush();
        return engine;
    }

    private static void run(String[] a) throws Exception {
        Config cfg = Config.load(Path.of(a[2]));
        LgbmModel model = LgbmModel.load(Path.of(a[3]));
        Path out = Path.of(a[4]);
        Files.createDirectories(out);
        EventReader.Events ev = EventReader.read(Path.of(a[1]), a.length > 5 ? Integer.parseInt(a[5]) : Integer.MAX_VALUE);
        try (BufferedWriter features = Files.newBufferedWriter(out.resolve("features.csv"))) {
            features.write("trader,instrument,window," + String.join(",", FeatureExtractor.FEATURE_NAMES)
                    + ",p0,p1,p2,p3\n");
            Engine.RowSink sink = (row, proba) -> {
                StringBuilder sb = new StringBuilder();
                sb.append(row.trader).append(',').append(row.instrument).append(',').append(row.window);
                for (double v : row.values) {
                    sb.append(',').append(v);
                }
                for (double p : proba) {
                    sb.append(',').append(p);
                }
                try {
                    features.write(sb.append('\n').toString());
                } catch (IOException e) {
                    throw new RuntimeException(e);
                }
            };
            Engine engine = feed(ev, cfg, model, sink);
            try (BufferedWriter alerts = Files.newBufferedWriter(out.resolve("alerts.csv"))) {
                alerts.write("detector,kind,trader,instrument,window,score\n");
                for (Engine.Alert x : engine.alerts) {
                    alerts.write(x.detector + "," + x.kind + "," + x.trader + "," + x.instrument + ","
                            + x.window + "," + x.score + "\n");
                }
            }
        }
    }

    private static void bench(String[] a) throws Exception {
        Config cfg = Config.load(Path.of(a[2]));
        LgbmModel model = LgbmModel.load(Path.of(a[3]));
        EventReader.Events ev = EventReader.read(Path.of(a[1]), a.length > 4 ? Integer.parseInt(a[4]) : Integer.MAX_VALUE);

        for (int warmup = 0; warmup < 2; warmup++) {
            feed(ev, cfg, model, null);  // unmeasured: lets the JIT compile the hot paths
        }
        long t0 = System.nanoTime();
        Engine measured = feed(ev, cfg, model, null);
        double seconds = (System.nanoTime() - t0) / 1e9;

        Engine engine = new Engine(cfg, model, null);
        long[] d = ev.data;
        long[] lat = new long[ev.n];
        boolean[] closes = new boolean[ev.n];
        for (int i = 0; i < ev.n; i++) {
            int o = i * F;
            long start = System.nanoTime();
            int closed = engine.process(d[o], (int) d[o + 1], (int) d[o + 2], d[o + 3], d[o + 4], d[o + 5],
                    (int) d[o + 6], d[o + 7], d[o + 8], d[o + 9], d[o + 10], d[o + 11], d[o + 12], d[o + 13], d[o + 14]);
            long end = System.nanoTime();
            lat[i] = end - start;
            closes[i] = closed > 0;
        }
        long overhead = timerOverhead();
        int nClose = 0;
        for (boolean c : closes) {
            if (c) {
                nClose++;
            }
        }
        long[] all = new long[ev.n];
        long[] close = new long[nClose];
        long[] other = new long[ev.n - nClose];
        int ci = 0;
        int oi = 0;
        for (int i = 0; i < ev.n; i++) {
            all[i] = Math.max(0, lat[i] - overhead);
            if (closes[i]) {
                close[ci++] = all[i];
            } else {
                other[oi++] = all[i];
            }
        }
        System.out.println(String.format(Locale.ROOT,
                "{\"engine\":\"java\",\"n_events\":%d,\"throughput_eps\":%.1f,\"timer_overhead_ns\":%d,"
                        + "\"rows_scored\":%d,\"n_alerts\":%d,\"latency_ns\":{\"overall\":%s,\"window_close\":%s,\"other\":%s}}",
                ev.n, ev.n / seconds, overhead, measured.rowsScored, measured.alerts.size(),
                summary(all), summary(close), summary(other)));
    }

    private static long timerOverhead() {
        long[] diffs = new long[200_000];
        for (int i = 0; i < diffs.length; i++) {
            long x = System.nanoTime();
            long y = System.nanoTime();
            diffs[i] = y - x;
        }
        Arrays.sort(diffs);
        return diffs[diffs.length / 2];
    }

    /** JSON object with n, median, p99, p999, max using nearest-rank percentiles. */
    private static String summary(long[] values) {
        if (values.length == 0) {
            return "{\"n\":0,\"median\":0,\"p99\":0,\"p999\":0,\"max\":0}";
        }
        long[] s = values.clone();
        Arrays.sort(s);
        return String.format(Locale.ROOT, "{\"n\":%d,\"median\":%d,\"p99\":%d,\"p999\":%d,\"max\":%d}",
                s.length, nearestRank(s, 0.5), nearestRank(s, 0.99), nearestRank(s, 0.999), s[s.length - 1]);
    }

    private static long nearestRank(long[] sorted, double p) {
        int index = Math.max(0, (int) Math.ceil(p * sorted.length) - 1);
        return sorted[index];
    }
}
