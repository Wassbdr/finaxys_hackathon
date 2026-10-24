package sentinel;

import java.nio.file.Files;
import java.nio.file.Path;

/** Checks the tree evaluator against a fixture: each line is 22 features then 4 probabilities. */
public final class SelfTest {
    public static void main(String[] args) throws Exception {
        LgbmModel model = LgbmModel.load(Path.of(args[0]));
        double[] p = new double[model.numClass()];
        int cases = 0;
        int bad = 0;
        for (String line : Files.readAllLines(Path.of(args[1]))) {
            if (line.isBlank()) {
                continue;
            }
            String[] f = line.split(",");
            double[] x = new double[f.length - model.numClass()];
            for (int i = 0; i < x.length; i++) {
                x[i] = Double.parseDouble(f[i]);
            }
            model.predict(x, p);
            for (int k = 0; k < p.length; k++) {
                double expected = Double.parseDouble(f[x.length + k]);
                if (Math.abs(p[k] - expected) > 1e-9 * Math.max(1.0, Math.abs(expected))) {
                    bad++;
                }
            }
            cases++;
        }
        System.out.println(cases + " cases, " + bad + " mismatches");
        if (bad > 0 || cases == 0) {
            System.exit(1);
        }
    }
}
