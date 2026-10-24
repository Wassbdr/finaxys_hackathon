package sentinel;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/** Evaluator for LightGBM multiclass models saved in the text format (numerical splits only). */
public final class LgbmModel {
    private static final double ZERO_THRESHOLD = 1e-35;
    private static final int MISSING_ZERO = 1;
    private static final int MISSING_NAN = 2;

    private static final class Tree {
        int numLeaves;
        int[] splitFeature = new int[0];
        double[] threshold = new double[0];
        int[] decisionType = new int[0];
        int[] left = new int[0];
        int[] right = new int[0];
        double[] leafValue = new double[0];
    }

    private final List<Tree> trees = new ArrayList<>();
    private int numClass = 1;

    // All trees flattened into contiguous arrays (one walk touches few cache lines).
    // Child references: >= 0 is a global internal-node index, < 0 is ~(global leaf index).
    private int[] root;         // first node of each tree, or -1 for a single-leaf tree
    private double[] constant;  // value of a single-leaf tree
    private int[] feature;
    private double[] threshold;
    private int[] decisionType;
    private int[] left;
    private int[] right;
    private double[] leaf;

    private LgbmModel() {
    }

    /** Parse a model file written by LightGBM's save_model. */
    public static LgbmModel load(Path path) throws IOException {
        LgbmModel model = new LgbmModel();
        Tree current = null;
        for (String raw : Files.readAllLines(path)) {
            String line = raw.trim();
            if (line.startsWith("Tree=")) {
                current = new Tree();
                model.trees.add(current);
                continue;
            }
            if (line.equals("end of trees")) {
                break;
            }
            int eq = line.indexOf('=');
            if (eq < 0) {
                continue;
            }
            String key = line.substring(0, eq);
            String value = line.substring(eq + 1);
            if (current == null) {
                if (key.equals("num_class")) {
                    model.numClass = Integer.parseInt(value);
                }
                continue;
            }
            switch (key) {
                case "num_leaves" -> current.numLeaves = Integer.parseInt(value);
                case "split_feature" -> current.splitFeature = ints(value);
                case "threshold" -> current.threshold = doubles(value);
                case "decision_type" -> current.decisionType = ints(value);
                case "left_child" -> current.left = ints(value);
                case "right_child" -> current.right = ints(value);
                case "leaf_value" -> current.leafValue = doubles(value);
                default -> { }
            }
        }
        for (Tree tree : model.trees) {
            for (int type : tree.decisionType) {
                if ((type & 1) != 0) {
                    throw new UnsupportedOperationException("categorical splits are not supported");
                }
            }
        }
        if (model.trees.isEmpty() || model.trees.size() % model.numClass != 0) {
            throw new IllegalStateException("unexpected tree count " + model.trees.size());
        }
        model.flatten();
        return model;
    }

    private void flatten() {
        int nodes = 0;
        int leaves = 0;
        for (Tree t : trees) {
            nodes += t.numLeaves > 1 ? t.splitFeature.length : 0;
            leaves += t.leafValue.length;
        }
        root = new int[trees.size()];
        constant = new double[trees.size()];
        feature = new int[nodes];
        threshold = new double[nodes];
        decisionType = new int[nodes];
        left = new int[nodes];
        right = new int[nodes];
        leaf = new double[leaves];
        int nodeBase = 0;
        int leafBase = 0;
        for (int i = 0; i < trees.size(); i++) {
            Tree t = trees.get(i);
            System.arraycopy(t.leafValue, 0, leaf, leafBase, t.leafValue.length);
            if (t.numLeaves <= 1) {
                root[i] = -1;
                constant[i] = t.leafValue[0];
            } else {
                root[i] = nodeBase;
                for (int j = 0; j < t.splitFeature.length; j++) {
                    feature[nodeBase + j] = t.splitFeature[j];
                    threshold[nodeBase + j] = t.threshold[j];
                    decisionType[nodeBase + j] = t.decisionType[j];
                    left[nodeBase + j] = rebase(t.left[j], nodeBase, leafBase);
                    right[nodeBase + j] = rebase(t.right[j], nodeBase, leafBase);
                }
                nodeBase += t.splitFeature.length;
            }
            leafBase += t.leafValue.length;
        }
    }

    private static int rebase(int child, int nodeBase, int leafBase) {
        return child >= 0 ? child + nodeBase : ~(~child + leafBase);
    }

    private double evalTree(int tree, double[] x) {
        int node = root[tree];
        if (node < 0) {
            return constant[tree];
        }
        while (node >= 0) {
            double v = x[feature[node]];
            int type = decisionType[node];
            boolean goLeft;
            if ((type & 12) == 0) {
                // Missing type "none" (what LightGBM writes when training data has no NaN):
                // NaN is read as 0.0, then a plain <= test.
                goLeft = (v != v ? 0.0 : v) <= threshold[node];
            } else {
                int missingType = (type >> 2) & 3;
                if (Double.isNaN(v) && missingType != MISSING_NAN) {
                    v = 0.0;
                }
                if ((missingType == MISSING_ZERO && Math.abs(v) <= ZERO_THRESHOLD)
                        || (missingType == MISSING_NAN && Double.isNaN(v))) {
                    goLeft = (type & 2) != 0;
                } else {
                    goLeft = v <= threshold[node];
                }
            }
            node = goLeft ? left[node] : right[node];
        }
        return leaf[~node];
    }

    public int numClass() {
        return numClass;
    }

    /** Raw (pre-softmax) class scores into {@code out}; tree i contributes to class i mod numClass. */
    public void rawScores(double[] x, double[] out) {
        java.util.Arrays.fill(out, 0, numClass, 0.0);
        for (int i = 0; i < root.length; i++) {
            out[i % numClass] += evalTree(i, x);
        }
    }

    /** Class probabilities into {@code out} (numerically stable softmax, as LightGBM computes it). */
    public void predict(double[] x, double[] out) {
        rawScores(x, out);
        double max = out[0];
        for (int k = 1; k < numClass; k++) {
            max = Math.max(max, out[k]);
        }
        double sum = 0.0;
        for (int k = 0; k < numClass; k++) {
            out[k] = Math.exp(out[k] - max);
            sum += out[k];
        }
        for (int k = 0; k < numClass; k++) {
            out[k] /= sum;
        }
    }

    private static int[] ints(String s) {
        if (s.isBlank()) {
            return new int[0];
        }
        String[] parts = s.trim().split(" ");
        int[] out = new int[parts.length];
        for (int i = 0; i < parts.length; i++) {
            out[i] = Integer.parseInt(parts[i]);
        }
        return out;
    }

    private static double[] doubles(String s) {
        if (s.isBlank()) {
            return new double[0];
        }
        String[] parts = s.trim().split(" ");
        double[] out = new double[parts.length];
        for (int i = 0; i < parts.length; i++) {
            out[i] = parseDouble(parts[i]);
        }
        return out;
    }

    private static double parseDouble(String s) {
        return switch (s) {
            case "inf", "+inf" -> Double.POSITIVE_INFINITY;
            case "-inf" -> Double.NEGATIVE_INFINITY;
            case "nan" -> Double.NaN;
            default -> Double.parseDouble(s);
        };
    }
}
