"""Run the evaluation.

    python eval/run_eval.py

For every (question, language, arm, model) cell the same question is
asked N_RUNS times. Each response is resolved to a number, scored
against the governed ground truth, labeled with the interpretation it
corresponds to, and priced from API-reported token usage.

Three things are being measured:
  correctness   did the model land on the governed definition?
  consistency   does the same setup produce the same number every time?
  economics     what does a correct answer cost through each interface?

The semantic layer is expected to win on all three, not because the
model got smarter, but because the interface shrank the space of
possible interpretations, moved execution into a governed engine, and
stopped paying to re-ship raw context on every question. Watch the
documented_sql arm closely: it is the steelman of a traditional
warehouse, and where it holds and where it slips is the whole argument.
The pt rows test language invariance. The cost tables report the metric
that matters: cost per CORRECT answer, since a cheap arm that answers
wrong is an expensive arm.
"""

import csv
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import arms
import config
import costing
import providers

ROOT = Path(__file__).resolve().parents[1]

FP_TITLES = {
    "active_count": "Active users (count)",
    "active_share": "Active share (percent)",
    "mau": "Monthly active users, June 2026 (count)",
    "wau": "Weekly active users, week of June 22, 2026 (count)",
}
FP_KINDS = {
    "active_count": "count", "active_share": "percent",
    "mau": "count", "wau": "count",
}
FP_GT_KEYS = {
    "active_count": "governed_active_users",
    "active_share": "governed_active / valid_universe",
    "mau": "governed_mau",
    "wau": "governed_wau",
}


def normalize(value, kind):
    if value is None:
        return None
    if kind == "percent" and 0 < value <= 1.0:
        return value * 100.0  # model answered with a fraction
    return float(value)


def is_correct(value, ground_truth, kind):
    if value is None:
        return False
    if kind == "count":
        return abs(value - ground_truth) < 1e-9
    return abs(value - ground_truth) <= config.TOLERANCE_PP


def label_for(value, fingerprints, kind):
    if value is None:
        return "error"
    if kind == "count":
        names = [k for k, v in fingerprints.items() if v == value]
    else:
        names = [
            k for k, v in fingerprints.items()
            if abs(v - value) <= config.TOLERANCE_PP
        ]
    return " / ".join(names) if names else "unrecognized interpretation"


def fmt(value, kind):
    if value is None:
        return "ERROR"
    if kind == "count":
        return str(int(value))
    return f"{value:.2f}%"


def run_one(arm, model, question_text, cube_meta):
    """Returns (value_or_None, raw_response, error_or_None, usage).

    Usage is captured as soon as the API replies, so calls whose SQL or
    JSON fails to resolve are still priced: wrong answers cost money too.
    """
    if arm == "raw_sql":
        prompt = arms.build_raw_sql_prompt(question_text)
    elif arm == "documented_sql":
        prompt = arms.build_documented_sql_prompt(question_text)
    elif arm == "csv_in_context":
        prompt = arms.build_csv_prompt(question_text)
    elif arm == "semantic_layer":
        prompt = arms.build_semantic_prompt(question_text, cube_meta)
    else:
        raise ValueError(f"Unknown arm: {arm}")

    raw = ""
    usage = {"input_tokens": 0, "output_tokens": 0}
    try:
        reply = providers.complete(
            model, prompt, config.TEMPERATURE, config.MAX_TOKENS,
            provider=config.PROVIDER_ROUTING.get(model),
        )
        raw = reply["text"]
        usage = {
            "input_tokens": reply["input_tokens"],
            "output_tokens": reply["output_tokens"],
        }
        if arm == "raw_sql":
            value = arms.resolve_sql(raw, modeled=False)
        elif arm == "documented_sql":
            value = arms.resolve_sql(raw, modeled=True)
        elif arm == "csv_in_context":
            value = arms.resolve_csv(raw)
        else:
            value = arms.resolve_semantic(raw, config.CUBE_URL, config.CUBE_TOKEN)
        return value, raw, None, usage
    except Exception as exc:  # noqa: BLE001 - record and continue
        return None, raw, f"{type(exc).__name__}: {exc}", usage


def main():
    missing = [m for m in config.MODELS if m not in config.PRICING]
    if missing:
        sys.exit(f"No pricing for {missing}. Add entries to PRICING in eval/config.py.")

    count_fp = arms.compute_count_fingerprints()
    share_fp = arms.compute_share_fingerprints(count_fp)
    mau_fp = arms.compute_mau_fingerprints(count_fp)
    wau_fp = arms.compute_wau_fingerprints(count_fp, mau_fp)
    fp_sets = {
        "active_count": count_fp, "active_share": share_fp,
        "mau": mau_fp, "wau": wau_fp,
    }
    ground_truths = {
        key: float(fp_sets[key][FP_GT_KEYS[key]]) for key in fp_sets
    }
    print(f"Ground truth, active users:       {int(ground_truths['active_count'])}")
    print(f"Ground truth, active share:       {ground_truths['active_share']:.4f}%")
    print(f"Ground truth, MAU June 2026:      {int(ground_truths['mau'])}")
    print(f"Ground truth, WAU week of Jun 22: {int(ground_truths['wau'])}\n")

    needs_cube = any("semantic_layer" in q["arms"] for q in config.QUESTIONS)
    cube_meta = None
    if needs_cube:
        try:
            cube_meta = arms.fetch_cube_meta(config.CUBE_URL, config.CUBE_TOKEN)
        except Exception as exc:  # noqa: BLE001
            print(f"Cannot reach Cube at {config.CUBE_URL} ({exc}).")
            print("Start it with `docker compose up -d`, or remove "
                  "'semantic_layer' from the arms in eval/config.py.")
            sys.exit(1)

    records = []
    total = (
        sum(len(q["arms"]) for q in config.QUESTIONS)
        * len(config.LANGUAGES) * len(config.MODELS) * config.N_RUNS
    )
    done = 0
    spend = 0.0
    for q in config.QUESTIONS:
        kind = q["kind"]
        fp = fp_sets[q["fp"]]
        gt = ground_truths[q["fp"]]
        for lang in config.LANGUAGES:
            text = q["text"][lang]
            for arm in q["arms"]:
                for model in config.MODELS:
                    for run in range(1, config.N_RUNS + 1):
                        value, raw, error, usage = run_one(arm, model, text, cube_meta)
                        value = normalize(value, kind)
                        cost = costing.call_cost(
                            config.PRICING, model,
                            usage["input_tokens"], usage["output_tokens"],
                        )
                        spend += cost
                        done += 1
                        shown = error if error else fmt(value, kind)
                        print(f"[{done:3d}/{total}] {q['id']:16s} {lang}  "
                              f"{arm:16s} {model:28s} run {run}: {shown}")
                        records.append({
                            "question": q["id"],
                            "lang": lang,
                            "kind": kind,
                            "arm": arm,
                            "model": model,
                            "run": run,
                            "value": value,
                            "correct": is_correct(value, gt, kind),
                            "interpretation": label_for(value, fp, kind),
                            "input_tokens": usage["input_tokens"],
                            "output_tokens": usage["output_tokens"],
                            "cost_usd": cost,
                            "error": error or "",
                            "raw_response": raw.replace("\n", "\\n")[:500],
                        })

    print(f"\nTotal spend: {costing.fmt_usd(spend)} across {done} calls")
    write_outputs(records, fp_sets, ground_truths)


def _all_arms():
    seen = []
    for q in config.QUESTIONS:
        for arm in q["arms"]:
            if arm not in seen:
                seen.append(arm)
    return seen


def write_outputs(records, fp_sets, ground_truths):
    out_dir = ROOT / config.RESULTS_DIR
    out_dir.mkdir(exist_ok=True)

    csv_path = out_dir / "results.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)

    lines = [
        "# Semantic layer eval report",
        f"\nGenerated: {datetime.now().isoformat(timespec='seconds')}",
    ]

    print()
    for q in config.QUESTIONS:
        kind = q["kind"]
        fp = fp_sets[q["fp"]]
        gt = ground_truths[q["fp"]]
        lines += [
            f"\n## {q['text']['en']}",
            f"\n(pt: {q['text']['pt']})",
            f"\nGround truth (governed definition): **{fmt(gt, kind)}**\n",
            "| lang | arm | model | correct | distinct | answers (count x value = interpretation) |",
            "|---|---|---|---|---|---|",
        ]
        print("=" * 118)
        print(f"{q['text']['en']}   (ground truth: {fmt(gt, kind)})")
        print("-" * 118)
        print(f"{'lang':6s}{'arm':18s}{'model':30s}{'correct':10s}{'distinct':10s}answers")
        print("-" * 118)
        for lang in config.LANGUAGES:
            for arm in q["arms"]:
                for model in config.MODELS:
                    group = [
                        r for r in records
                        if r["question"] == q["id"] and r["lang"] == lang
                        and r["arm"] == arm and r["model"] == model
                    ]
                    n = len(group)
                    n_correct = sum(r["correct"] for r in group)
                    answers = Counter(fmt(r["value"], kind) for r in group)
                    detail = "; ".join(
                        f"{cnt}x {val}" + (
                            "" if val == "ERROR" else
                            f" = {next(r['interpretation'] for r in group if fmt(r['value'], kind) == val)}"
                        )
                        for val, cnt in answers.most_common()
                    )
                    lines.append(
                        f"| {lang} | {arm} | {model} | {n_correct}/{n} "
                        f"| {len(answers)} | {detail} |"
                    )
                    print(f"{lang:6s}{arm:18s}{model:30s}{f'{n_correct}/{n}':10s}"
                          f"{len(answers)!s:10s}{detail}")
        print()

    # ------------------------------------------------------- token footprint
    lines += [
        "\n## Token footprint (price-independent)",
        "\nTokens are the structural property of each interface; prices change,",
        "footprints do not. Input per call is what an arm must ship to ask one",
        "question: schema, docs, and samples for the SQL arms, the dataset",
        "itself for the CSV arm, the measure list for the semantic arm. The",
        "footprint also bounds agentic headroom (every token spent asking is",
        "context an agent cannot spend on tools, history, or reasoning) and",
        "org throughput (rate limits are denominated in tokens per minute).",
        "\n### By question and arm (all models, both languages)\n",
        "| question | arm | calls | input tok / call | output tok / call | input vs semantic |",
        "|---|---|---|---|---|---|",
    ]
    print("=" * 118)
    print("Token footprint (price-independent)")
    print("-" * 118)
    print(f"{'question':18s}{'arm':18s}{'calls':7s}{'in/call':10s}"
          f"{'out/call':10s}input vs semantic")
    print("-" * 118)
    for q in config.QUESTIONS:
        stats = {
            arm: costing.aggregate([
                r for r in records
                if r["question"] == q["id"] and r["arm"] == arm
            ])
            for arm in q["arms"]
        }
        base = stats.get("semantic_layer", {}).get("mean_input") or None
        for arm in q["arms"]:
            a = stats[arm]
            ratio = (
                f"{a['mean_input'] / base:.1f}x"
                if base and a["calls"] else "n/a"
            )
            lines.append(
                f"| {q['id']} | {arm} | {a['calls']} "
                f"| {a['mean_input']:,.0f} | {a['mean_output']:,.0f} | {ratio} |"
            )
            print(f"{q['id']:18s}{arm:18s}{a['calls']:<7d}"
                  f"{a['mean_input']:<10,.0f}{a['mean_output']:<10,.0f}{ratio}")

    lines += [
        "\n### Rollup by arm (all questions, models, and languages)\n",
        "| arm | calls | input tokens | output tokens | tokens / correct |",
        "|---|---|---|---|---|",
    ]
    print("-" * 118)
    print(f"{'arm':18s}{'calls':7s}{'in total':13s}{'out total':13s}tokens/correct")
    for arm in _all_arms():
        a = costing.aggregate([r for r in records if r["arm"] == arm])
        tpc = ("n/a" if a["tokens_per_correct"] is None
               else f"{a['tokens_per_correct']:,.0f}")
        lines.append(
            f"| {arm} | {a['calls']} | {a['input_tokens']:,} "
            f"| {a['output_tokens']:,} | {tpc} |"
        )
        print(f"{arm:18s}{a['calls']:<7d}{a['input_tokens']:<13,d}"
              f"{a['output_tokens']:<13,d}{tpc}")
    print()

    # ------------------------------------------------------------------ cost
    lines += [
        "\n## Cost",
        "\nToken footprints priced at the standard rates pinned in",
        "eval/config.py (no caching, no batch discounts). Rates change;",
        "verify against the official pricing page before quoting numbers.",
        "The headline metric is cost per CORRECT answer: an arm that is",
        "cheap per call but wrong is expensive per usable number. Wasted $",
        "is spend on runs that did not produce the governed answer.",
        "\n### By arm and model (all questions, both languages)\n",
        "| arm | model | calls | correct | input tok | output tok | total | $ / correct | wasted |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    print("=" * 118)
    print("Cost   (headline metric: $ per correct answer)")
    print("-" * 118)
    print(f"{'arm':18s}{'model':30s}{'calls':7s}{'correct':9s}{'in tok':10s}"
          f"{'out tok':9s}{'total':11s}{'$/correct':12s}wasted")
    print("-" * 118)
    for arm in _all_arms():
        for model in config.MODELS:
            group = [r for r in records if r["arm"] == arm and r["model"] == model]
            if not group:
                continue
            a = costing.aggregate(group)
            lines.append(
                f"| {arm} | {model} | {a['calls']} | {a['correct']} "
                f"| {a['input_tokens']:,} | {a['output_tokens']:,} "
                f"| {costing.fmt_usd(a['cost'])} "
                f"| {costing.fmt_usd(a['cost_per_correct'])} "
                f"| {costing.fmt_usd(a['wasted'])} |"
            )
            print(f"{arm:18s}{model:30s}{a['calls']:<7d}{a['correct']:<9d}"
                  f"{a['input_tokens']:<10,d}{a['output_tokens']:<9,d}"
                  f"{costing.fmt_usd(a['cost']):11s}"
                  f"{costing.fmt_usd(a['cost_per_correct']):12s}"
                  f"{costing.fmt_usd(a['wasted'])}")

    lines += [
        "\n### By question and arm (all models, both languages)\n",
        "| question | arm | calls | correct | total | $ / correct |",
        "|---|---|---|---|---|---|",
    ]
    for q in config.QUESTIONS:
        for arm in q["arms"]:
            group = [r for r in records
                     if r["question"] == q["id"] and r["arm"] == arm]
            a = costing.aggregate(group)
            lines.append(
                f"| {q['id']} | {arm} | {a['calls']} | {a['correct']} "
                f"| {costing.fmt_usd(a['cost'])} "
                f"| {costing.fmt_usd(a['cost_per_correct'])} |"
            )

    grand = costing.aggregate(records)
    total_line = (
        f"Grand total: {grand['calls']} calls, "
        f"{grand['input_tokens']:,} input tokens, "
        f"{grand['output_tokens']:,} output tokens, "
        f"{costing.fmt_usd(grand['cost'])} "
        f"({costing.fmt_usd(grand['wasted'])} on non-governed answers)"
    )
    lines.append(f"\n{total_line}")
    print("-" * 118)
    print(total_line)

    # ---------------------------------------------------------- fingerprints
    lines += [
        "\n## Interpretation fingerprints",
        "\nEvery plausible reading maps to a distinct value, so each answer",
        "identifies the definition the model silently chose.\n",
    ]
    for key, title in FP_TITLES.items():
        kind = FP_KINDS[key]
        lines += [f"\n### {title}\n", "| value | interpretation |", "|---|---|"]
        for k, v in sorted(fp_sets[key].items(), key=lambda kv: -kv[1]):
            mark = " **(ground truth)**" if k == FP_GT_KEYS[key] else ""
            lines.append(f"| {fmt(v, kind)} | {k}{mark} |")

    report_path = out_dir / "report.md"
    report_path.write_text("\n".join(lines) + "\n")
    print(f"\nWrote {csv_path}")
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
