package sentinel;

import java.io.IOException;
import java.io.Reader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Properties;

/** engine.properties: window length, model thresholds and rules. */
public final class Config {
    /** One threshold test on one feature. */
    public static final class Condition {
        final int feature;
        final boolean atLeast;
        final double threshold;

        Condition(int feature, boolean atLeast, double threshold) {
            this.feature = feature;
            this.atLeast = atLeast;
            this.threshold = threshold;
        }

        boolean holds(double[] x) {
            return atLeast ? x[feature] >= threshold : x[feature] <= threshold;
        }
    }

    /** A conjunction ("all") or disjunction ("any") of conditions. */
    public static final class Rule {
        public final String detector;
        public final String kind;
        final boolean all;
        final Condition[] conditions;

        Rule(String detector, String kind, boolean all, Condition[] conditions) {
            this.detector = detector;
            this.kind = kind;
            this.all = all;
            this.conditions = conditions;
        }

        boolean flags(double[] x) {
            if (all) {
                for (Condition c : conditions) {
                    if (!c.holds(x)) {
                        return false;
                    }
                }
                return true;
            }
            for (Condition c : conditions) {
                if (c.holds(x)) {
                    return true;
                }
            }
            return false;
        }
    }

    public final int windowS;
    public final int nInstruments;
    /** Alert thresholds indexed by class (1 = SPOOFING, 2 = LAYERING, 3 = WASH); index 0 unused. */
    public final double[] thresholds = new double[4];
    public final List<Rule> rules = new ArrayList<>();

    private Config(int windowS, int nInstruments) {
        this.windowS = windowS;
        this.nInstruments = nInstruments;
    }

    public static Config load(Path path) throws IOException {
        Properties p = new Properties();
        try (Reader in = Files.newBufferedReader(path)) {
            p.load(in);
        }
        String[] features = p.getProperty("features").split(",");
        if (!java.util.Arrays.equals(features, FeatureExtractor.FEATURE_NAMES)) {
            throw new IllegalStateException("feature order in config differs from the Java extractor");
        }
        Config c = new Config(Integer.parseInt(p.getProperty("window_s")),
                p.getProperty("instruments").split(",").length);
        String[] kinds = {"", "SPOOFING", "LAYERING", "WASH"};
        for (int k = 1; k < 4; k++) {
            c.thresholds[k] = Double.parseDouble(p.getProperty("threshold." + kinds[k]));
        }
        int count = Integer.parseInt(p.getProperty("rule.count"));
        for (int i = 0; i < count; i++) {
            String[] parts = p.getProperty("rule." + i + ".conditions").split(";");
            Condition[] conditions = new Condition[parts.length];
            for (int j = 0; j < parts.length; j++) {
                conditions[j] = parseCondition(parts[j]);
            }
            c.rules.add(new Rule(p.getProperty("rule." + i + ".detector"), p.getProperty("rule." + i + ".kind"),
                    p.getProperty("rule." + i + ".mode").equals("all"), conditions));
        }
        return c;
    }

    private static Condition parseCondition(String text) {
        boolean atLeast = text.contains(">=");
        int at = text.indexOf(atLeast ? ">=" : "<=");
        String name = text.substring(0, at);
        double threshold = Double.parseDouble(text.substring(at + 2));
        for (int i = 0; i < FeatureExtractor.FEATURE_NAMES.length; i++) {
            if (FeatureExtractor.FEATURE_NAMES[i].equals(name)) {
                return new Condition(i, atLeast, threshold);
            }
        }
        throw new IllegalArgumentException("unknown feature in rule: " + name);
    }
}
