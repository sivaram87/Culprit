// Browser port of job/detect.py for the website simulator. Keep in sync with the Python rules.
(function (root) {
  const DEFAULTS = { minUsers: 500, minCrashes: 10, minRatio: 2.0, alpha: 0.01 };
  const KILL_SIGNALS = new Set(["FATAL", "ANR"]);

  // Complementary error function (Numerical Recipes erfcc): relative error < 1.2e-7 everywhere,
  // so tiny p-values stay meaningful.
  function erfc(x) {
    const z = Math.abs(x);
    const t = 1 / (1 + 0.5 * z);
    const r = t * Math.exp(-z * z - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (0.09678418 +
      t * (-0.18628806 + t * (0.27886807 + t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223 +
      t * 0.17087277)))))))));
    return x >= 0 ? r : 2 - r;
  }

  // One-sided two-proportion z-test, same as detect.p_value.
  function pValue(c1, n1, c0, n0) {
    const pooled = (c1 + c0) / (n1 + n0);
    const se = Math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n0));
    if (!se) return 1;
    return 0.5 * erfc((c1 / n1 - c0 / n0) / se / Math.SQRT2);
  }

  // Always-valid p-value (mixture SPRT), same as detect.msprt_p: safe to check after every run.
  function msprtP(c1, n1, c0, n0, tau2) {
    const pooled = (c1 + c0) / (n1 + n0);
    const v = pooled * (1 - pooled) * (1 / n1 + 1 / n0);
    const d = c1 / n1 - c0 / n0;
    if (!v || d <= 0) return 1;
    const logLr = 0.5 * Math.log(v / (v + tau2)) + (tau2 * d * d) / (2 * v * (v + tau2));
    return Math.min(1, Math.exp(-logLr));
  }

  // Every check detect() runs for one variant-vs-control comparison.
  function evaluate({ users1, crashes1, users0, crashes0, comparisons = 1 }, t = DEFAULTS) {
    const c1 = Math.min(crashes1, users1), c0 = Math.min(crashes0, users0);
    const rate = users1 ? c1 / users1 : 0, base = users0 ? c0 / users0 : 0;
    const threshold = t.alpha / Math.max(1, comparisons);
    const tau2 = ((c1 + c0) / (users1 + users0) * (t.minRatio - 1)) ** 2;
    const p = users1 && users0 ? msprtP(c1, users1, c0, users0, tau2) : 1;
    const checks = [
      { id: "users", label: `At least ${t.minUsers} users in each group`, ok: Math.min(users1, users0) >= t.minUsers },
      { id: "crashes", label: `At least ${t.minCrashes} affected users in the variant`, ok: c1 >= t.minCrashes },
      { id: "ratio", label: `Variant rate at least ${t.minRatio}× control`, ok: rate >= t.minRatio * base },
      { id: "p", label: `p below ${fmtExp(threshold)} (${t.alpha} ÷ ${Math.max(1, comparisons)} comparisons)`, ok: p < threshold },
    ];
    return { rate, base, p, threshold, checks, flagged: checks.every((c) => c.ok) };
  }

  // Same policy as detect.action_for.
  function actionFor(signal, autoKill, killsToday, maxKills = 1) {
    if (!autoKill || !KILL_SIGNALS.has(signal)) return "alerted";
    return killsToday < maxKills ? "killed" : "kill_capped";
  }

  // Python's '{:.1e}' formatting (two-digit exponent), so the Slack preview matches the job.
  function fmtExp(x) {
    const [m, e] = x.toExponential(1).split("e");
    const n = Math.abs(+e);
    return `${m}e${+e < 0 ? "-" : "+"}${n < 10 ? "0" + n : n}`;
  }

  const api = { DEFAULTS, erfc, pValue, msprtP, evaluate, actionFor, fmtExp };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.CulpritDetect = api;
})(typeof window !== "undefined" ? window : globalThis);
