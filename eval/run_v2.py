"""Run steelman v2 (docs/steelman-v2-spec.md).

    uv run python eval/run_v2.py                 # first pass, all cells
    uv run python eval/run_v2.py --question C1_paying_active_share \\
        --mode agentic --model openai/gpt-6-luna --lang en --runs 1

Every cell (question, language, mode, arm, model) runs N_RUNS times.
Each episode resolves to a number or a {group: number} breakdown and is
scored three ways: correct, silent_wrong (resolved, not governed), or
loud_error (the harness could see the failure). api_error marks
transport failures; they are reported but excluded from accuracy.

Outputs in results/v2/: results.csv (one row per episode), report.md,
and transcripts/ (one JSON file per episode).
"""

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import costing  # noqa: E402
import providers  # noqa: E402
import v2_agent  # noqa: E402
import v2_arms  # noqa: E402
import v2_config as cfg  # noqa: E402
import v2_fingerprints  # noqa: E402
from v2_arms import LoudError  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CATEGORY_TITLES = {
    "A": "Controls (artifact exists on both sides)",
    "B": "Metric x dimension slice",
    "C": "Cross-table ratio with no defined measure",
    "D": "Metric grouped by dimension",
    "E": "Time grain and multi-period",
    "F": "Threshold metric defined in prose only",
}


# ---------------------------------------------------------------- scoring

def normalize(value, kind):
    if kind == "percent" and isinstance(value, float) and 0 < value <= 1.0:
        return value * 100.0  # answered as a fraction
    return value


def _float_dict(d):
    return {k: float(v) for k, v in d.items()}


def matches(value, target, kind) -> bool:
    if value is None:
        return False
    if isinstance(target, dict) or isinstance(value, dict):
        return (isinstance(target, dict) and isinstance(value, dict)
                and _float_dict(value) == _float_dict(target))
    if kind == "percent":
        return abs(value - target) <= cfg.TOLERANCE_PP
    return abs(value - target) < 1e-9


def label_for(value, fingerprints, kind) -> str:
    names = [k for k, v in fingerprints.items() if matches(value, v, kind)]
    return " / ".join(names) if names else "unrecognized interpretation"


def fmt(value, kind) -> str:
    if value is None:
        return "ERROR"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {v:g}" for k, v in sorted(value.items())) + "}"
    if kind == "percent":
        return f"{value:.2f}%"
    return f"{value:g}"


def decisions_for(question, arm, mode):
    d = question["decisions"].get(arm)
    return d.get(mode) if isinstance(d, dict) else d


# ------------------------------------------------------------------- jobs

def build_jobs(args):
    jobs = []
    for q in cfg.QUESTIONS:
        if args.question and q["id"] not in args.question:
            continue
        for mode in cfg.MODES:
            if args.mode and mode not in args.mode:
                continue
            arm_names = cfg.ALL_ARMS if mode == "single_shot" else q["agentic_arms"]
            for lang in cfg.LANGUAGES:
                if args.lang and lang not in args.lang:
                    continue
                for arm in arm_names:
                    if args.arm and arm not in args.arm:
                        continue
                    for model in cfg.MODELS:
                        if args.model and model not in args.model:
                            continue
                        for run in range(1, (args.runs or cfg.N_RUNS) + 1):
                            jobs.append({"q": q, "mode": mode, "lang": lang,
                                         "arm": arm, "model": model, "run": run})
    return jobs


def run_single_shot(arm, question, kind, expected_keys, model, meta) -> dict:
    prompt = v2_arms.single_shot_prompt(arm, question, meta)
    state = {"value": None, "error": "", "error_kind": "", "tool_calls": 0,
             "input_tokens": 0, "output_tokens": 0, "answer_raw": "",
             "transcript": [{"role": "user", "content": prompt}]}
    try:
        reply = providers.complete(model, prompt, cfg.TEMPERATURE, cfg.MAX_TOKENS,
                                   provider=cfg.PROVIDER_ROUTING.get(model))
    except (RuntimeError, OSError) as exc:
        state["error"], state["error_kind"] = f"{type(exc).__name__}: {exc}", "api"
        return state
    state.update(input_tokens=reply["input_tokens"], output_tokens=reply["output_tokens"],
                 answer_raw=reply["text"])
    state["transcript"].append({"role": "assistant", "content": reply["text"]})
    try:
        state["value"] = v2_arms.resolve_single_shot(
            arm, reply["text"], kind, expected_keys, meta, cfg.CUBE_URL, cfg.CUBE_TOKEN)
    except LoudError as exc:
        state["error"], state["error_kind"] = str(exc), "loud"
    except (RuntimeError, OSError) as exc:  # Cube unreachable
        state["error"], state["error_kind"] = f"{type(exc).__name__}: {exc}", "api"
    return state


def execute(job, fp_sets, meta, transcripts_dir) -> dict:
    q, mode, arm_name, model = job["q"], job["mode"], job["arm"], job["model"]
    arm = v2_arms.ARMS[arm_name]
    kind = q["kind"]
    fp = fp_sets[q["fp"]]
    gt = fp[cfg.GROUND_TRUTH_KEYS[q["fp"]]]
    expected_keys = list(gt.keys()) if isinstance(gt, dict) else None
    text = q["text"][job["lang"]]

    if mode == "single_shot":
        state = run_single_shot(arm, text, kind, expected_keys, model, meta)
    else:
        state = v2_agent.run_episode(arm, text, kind, expected_keys, model, meta)

    value = normalize(state["value"], kind)
    if state["error_kind"] == "api":
        outcome = "api_error"
    elif state["error_kind"] == "loud" or value is None:
        outcome = "loud_error"
    elif matches(value, gt, kind):
        outcome = "correct"
    else:
        outcome = "silent_wrong"

    slug = re.sub(r"[^A-Za-z0-9.-]+", "_", f"{q['id']}_{job['lang']}_{mode}_{arm_name}_{model}_{job['run']}")
    transcript_path = transcripts_dir / f"{slug}.json"
    transcript_path.write_text(json.dumps({
        "job": {k: job[k] for k in ("mode", "lang", "arm", "model", "run")} | {"question": q["id"]},
        "outcome": outcome, "value": value, "error": state["error"],
        "messages": state["transcript"],
    }, indent=2, default=str))

    cost = costing.call_cost(cfg.PRICING, model, state["input_tokens"], state["output_tokens"])
    return {
        "question": q["id"], "category": q["category"], "kind": kind,
        "lang": job["lang"], "mode": mode, "arm": arm_name, "model": model, "run": job["run"],
        "value": json.dumps(value) if value is not None else "",
        "correct": outcome == "correct",
        "outcome": outcome,
        "interpretation": label_for(value, fp, kind) if value is not None else "error",
        "decisions": decisions_for(q, arm_name, mode),
        "tool_calls": state["tool_calls"],
        "input_tokens": state["input_tokens"], "output_tokens": state["output_tokens"],
        "cost_usd": cost,
        "error": state["error"][:300],
        "answer_raw": str(state["answer_raw"]).replace("\n", "\\n")[:500],
        "transcript": str(transcript_path.relative_to(ROOT)),
        "_value": value,
    }


# ----------------------------------------------------------------- report

def scored(records):
    return [r for r in records if r["outcome"] != "api_error"]


def accuracy(records):
    s = scored(records)
    return (sum(r["correct"] for r in s) / len(s)) if s else None


def mean_distinct(records):
    """Mean count of distinct resolved answers per cell (question, lang,
    model). Loud errors are not resolved answers; a cell with no resolved
    answer has no consistency to measure and is left out, so failing
    loudly never reads as being consistent."""
    cells = defaultdict(set)
    for r in scored(records):
        if r["_value"] is not None:
            cells[(r["question"], r["lang"], r["model"])].add(
                json.dumps(r["_value"], sort_keys=True))
    return (sum(len(v) for v in cells.values()) / len(cells)) if cells else None


def pct(x):
    return "n/a" if x is None else f"{100 * x:.0f}%"


def num(x, digits=2):
    return "n/a" if x is None else f"{x:.{digits}f}"


def select(records, arm=None, mode=None, categories=None, question=None):
    return [r for r in records
            if (arm is None or r["arm"] == arm)
            and (mode is None or r["mode"] == mode)
            and (categories is None or r["category"] in categories)
            and (question is None or r["question"] == question)]


def falsifier_lines(records):
    lines = ["\n## Falsifiers (pre-registered)\n",
             f"Thresholds fixed before the first run (eval/v2_config.py): a documented arm "
             f"\"matches\" the layer when its accuracy is within {cfg.ACCURACY_MARGIN_PP:g}pp "
             f"of the layer's or above, AND its mean distinct answers per cell is within "
             f"{cfg.DISTINCT_MARGIN:g} of the layer's or below. Agentic semantic \"recovers\" "
             f"category C at accuracy >= {cfg.RECOVERY_THRESHOLD:.0%}.\n",
             "| falsifier | comparison | documented arm: accuracy, distinct | semantic_layer: accuracy, distinct | verdict |",
             "|---|---|---|---|---|"]

    def match_row(name, arm, mode, cats, note):
        a = select(records, arm=arm, mode=mode, categories=cats)
        s = select(records, arm="semantic_layer", mode=mode, categories=cats)
        acc_a, acc_s = accuracy(a), accuracy(s)
        dis_a, dis_s = mean_distinct(a), mean_distinct(s)
        if None in (acc_a, acc_s, dis_a, dis_s):
            verdict = "no data"
        else:
            hit = (100 * acc_a >= 100 * acc_s - cfg.ACCURACY_MARGIN_PP
                   and dis_a <= dis_s + cfg.DISTINCT_MARGIN)
            verdict = "**TRIGGERED**" if hit else "not triggered"
        lines.append(f"| {name} | {arm} vs semantic_layer, {mode}, {note} "
                     f"| {pct(acc_a)}, {num(dis_a)} | {pct(acc_s)}, {num(dis_s)} | {verdict} |")

    match_row("F1: views match the layer", "documented_views", "agentic",
              {"B", "C", "D", "E"}, "categories B-E")
    match_row("F2: prose alone matches the layer", "documented_prose", "single_shot",
              {"B", "C", "D", "E"}, "categories B-E")
    match_row("F2 (agentic check)", "documented_prose", "agentic", {"B"},
              "category B (prose runs agentic on A-B only)")

    single = accuracy(select(records, arm="semantic_layer", mode="single_shot", categories={"C"}))
    agentic = accuracy(select(records, arm="semantic_layer", mode="agentic", categories={"C"}))
    if single is None or agentic is None:
        verdict = "no data"
    else:
        hit = single < cfg.RECOVERY_THRESHOLD and agentic < cfg.RECOVERY_THRESHOLD
        verdict = "**TRIGGERED**" if hit else "not triggered"
    lines.append(f"| F3: layer cannot compose an undefined ratio | semantic_layer on C, "
                 f"single-shot vs agentic | single-shot {pct(single)} | agentic {pct(agentic)} "
                 f"| {verdict} |")
    return lines


def write_report(records, fp_sets, meta, out_dir, args):
    modes = [m for m in cfg.MODES if any(r["mode"] == m for r in records)]
    lines = [
        "# Steelman v2 report",
        f"\nGenerated: {datetime.now().isoformat(timespec='seconds')}. "
        f"Spec: docs/steelman-v2-spec.md.",
        f"\nModels: {', '.join(sorted({r['model'] for r in records}))}. "
        f"Languages: {', '.join(sorted({r['lang'] for r in records}))}. "
        f"Runs per cell: {args.runs or cfg.N_RUNS}. Episodes: {len(records)} "
        f"({sum(r['outcome'] == 'api_error' for r in records)} api errors, excluded from accuracy).",
        "\n## Authored artifacts (rule R1)\n",
        "Warehouse: one metrics_dictionary row per metric, two curated views frozen at v1 "
        "(vw_active_users, vw_qualifying_events). Cube: one measure per metric, plus one "
        "structural artifact declared once: the product_events -> users join, which lets any "
        "users dimension slice any event measure. No artifact was added for a v2 question.",
    ]
    if meta:
        lines.append(f"\nCube measures exposed: {', '.join(m['name'] for m in meta['measures'])}.")

    lines += falsifier_lines(records)

    # Summary by category
    lines += ["\n## Summary by category\n",
              "Accuracy excludes api errors. Distinct = mean distinct resolved answers per "
              "(question, language, model) cell, over cells with at least one resolved answer; "
              "lower is more consistent. Decisions = static count of silent choices (spec); "
              "n/a where one query cannot express the answer.\n",
              "| category | mode | arm | episodes | accuracy | distinct | silent wrong | loud error | decisions |",
              "|---|---|---|---|---|---|---|---|---|"]
    for cat in CATEGORY_TITLES:
        for mode in modes:
            for arm in cfg.ALL_ARMS:
                group = select(records, arm=arm, mode=mode, categories={cat})
                if not group:
                    continue
                s = scored(group)
                silent = sum(r["outcome"] == "silent_wrong" for r in s)
                loud = sum(r["outcome"] == "loud_error" for r in s)
                decisions = sorted({"n/a" if r["decisions"] is None else str(r["decisions"])
                                    for r in group})
                lines.append(
                    f"| {cat}: {CATEGORY_TITLES[cat]} | {mode} | {arm} | {len(group)} "
                    f"| {pct(accuracy(group))} | {num(mean_distinct(group))} "
                    f"| {silent} | {loud} | {', '.join(decisions)} |")

    # Consistency ranking
    lines += ["\n## Consistency ranking\n",
              "| mode | rank | arm | mean distinct answers per cell | accuracy |",
              "|---|---|---|---|---|"]
    for mode in modes:
        ranked = []
        for arm in cfg.ALL_ARMS:
            group = select(records, arm=arm, mode=mode)
            if group:
                ranked.append((mean_distinct(group), arm, accuracy(group)))
        ranked.sort(key=lambda t: (t[0] is None, t[0]))
        for i, (dis, arm, acc) in enumerate(ranked, 1):
            lines.append(f"| {mode} | {i} | {arm} | {num(dis)} | {pct(acc)} |")

    # Footprint and cost
    lines += ["\n## Footprint and cost\n",
              "Tokens are summed over every turn of an agentic episode. $ at the list rates "
              "pinned in eval/config.py.\n",
              "| mode | arm | episodes | input tok / episode | output tok / episode "
              "| tool calls / episode | total | $ / correct | wasted |",
              "|---|---|---|---|---|---|---|---|---|"]
    for mode in modes:
        for arm in cfg.ALL_ARMS:
            group = select(records, arm=arm, mode=mode)
            if not group:
                continue
            a = costing.aggregate(group)
            calls = sum(r["tool_calls"] for r in group) / len(group)
            lines.append(
                f"| {mode} | {arm} | {a['calls']} | {a['mean_input']:,.0f} "
                f"| {a['mean_output']:,.0f} | {calls:.1f} | {costing.fmt_usd(a['cost'])} "
                f"| {costing.fmt_usd(a['cost_per_correct'])} | {costing.fmt_usd(a['wasted'])} |")

    # Per question detail
    lines.append("\n## Per question\n")
    for q in cfg.QUESTIONS:
        group_q = select(records, question=q["id"])
        if not group_q:
            continue
        gt = fp_sets[q["fp"]][cfg.GROUND_TRUTH_KEYS[q["fp"]]]
        lines += [f"\n### {q['id']}: {q['text']['en']}",
                  f"\n(pt: {q['text']['pt']})",
                  f"\nGround truth: **{fmt(gt, q['kind'])}**\n",
                  "| lang | mode | arm | model | decisions | correct | distinct | loud | tool calls "
                  "| answers (count x value = interpretation) |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for mode in modes:
            for lang in cfg.LANGUAGES:
                for arm in cfg.ALL_ARMS:
                    for model in cfg.MODELS:
                        cell = [r for r in group_q if r["mode"] == mode and r["lang"] == lang
                                and r["arm"] == arm and r["model"] == model]
                        if not cell:
                            continue
                        answers = Counter(fmt(r["_value"], q["kind"]) if r["outcome"] != "api_error"
                                          else "API ERROR" for r in cell)
                        label = {fmt(r["_value"], q["kind"]): r["interpretation"] for r in cell}
                        detail = "; ".join(
                            f"{n}x {v}" + (f" = {label[v]}" if v in label and v != "ERROR" else "")
                            for v, n in answers.most_common())
                        resolved = {fmt(r["_value"], q["kind"]) for r in cell if r["_value"] is not None}
                        loud = sum(r["outcome"] == "loud_error" for r in cell)
                        calls = sum(r["tool_calls"] for r in cell) / len(cell)
                        lines.append(
                            f"| {lang} | {mode} | {arm} | {model} "
                            f"| {'n/a' if cell[0]['decisions'] is None else cell[0]['decisions']} "
                            f"| {sum(r['correct'] for r in cell)}/{len(cell)} | {len(resolved)} "
                            f"| {loud} | {calls:.1f} | {detail.replace('|', '/')} |")

    # Fingerprints
    lines += ["\n## Interpretation fingerprints\n",
              "Every plausible reading maps to a distinct value; vectors differ as whole vectors."]
    for q in cfg.QUESTIONS:
        fp = fp_sets[q["fp"]]
        gt_key = cfg.GROUND_TRUTH_KEYS[q["fp"]]
        lines += [f"\n### {q['id']}\n", "| value | interpretation |", "|---|---|"]
        for k, v in fp.items():
            mark = " **(ground truth)**" if k == gt_key else ""
            lines.append(f"| {fmt(v, q['kind'])} | {k}{mark} |")

    (out_dir / "report.md").write_text("\n".join(lines) + "\n")


# ------------------------------------------------------------------- main

def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--question", action="append", help="question id (repeatable)")
    p.add_argument("--mode", action="append", choices=cfg.MODES)
    p.add_argument("--arm", action="append", choices=cfg.ALL_ARMS)
    p.add_argument("--model", action="append")
    p.add_argument("--lang", action="append", choices=cfg.LANGUAGES)
    p.add_argument("--runs", type=int, help=f"runs per cell (default {cfg.N_RUNS})")
    p.add_argument("--out", default=cfg.RESULTS_DIR, help="output directory")
    return p.parse_args()


def main():
    args = parse_args()
    models = args.model or cfg.MODELS
    missing = [m for m in models if m not in cfg.PRICING]
    if missing:
        sys.exit(f"No pricing for {missing}. Add entries to PRICING in eval/config.py.")
    unknown = [m for m in models if m not in cfg.MODELS]
    if unknown:
        sys.exit(f"{unknown} not in v2_config.MODELS.")

    fp_sets = v2_fingerprints.compute_all()
    errors = v2_fingerprints.parity_errors(fp_sets)
    if errors:
        sys.exit("Fingerprint parity check failed (harness SQL vs generator):\n  "
                 + "\n  ".join(errors) + "\nRegenerate data or fix the mismatch.")
    print("Fingerprint parity: harness SQL matches the generator on every block.")

    jobs = build_jobs(args)
    if not jobs:
        sys.exit("No jobs match the filters.")
    meta = None
    if any(j["arm"] == "semantic_layer" for j in jobs):
        try:
            meta = v2_arms.fetch_cube_meta(cfg.CUBE_URL, cfg.CUBE_TOKEN)
        except Exception as exc:  # noqa: BLE001
            sys.exit(f"Cannot reach Cube at {cfg.CUBE_URL} ({exc}). "
                     "Start it with `docker compose up -d`.")

    out_dir = ROOT / args.out
    transcripts_dir = out_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    print(f"{len(jobs)} episodes, {cfg.WORKERS} workers, writing to {out_dir}\n")

    records, spend = [], 0.0
    with ThreadPoolExecutor(max_workers=cfg.WORKERS) as pool:
        futures = {pool.submit(execute, job, fp_sets, meta, transcripts_dir): job for job in jobs}
        for i, future in enumerate(as_completed(futures), 1):
            rec = future.result()
            records.append(rec)
            spend += rec["cost_usd"]
            shown = rec["error"][:80] if rec["outcome"] in ("loud_error", "api_error") else \
                fmt(rec["_value"], rec["kind"])
            print(f"[{i:4d}/{len(jobs)}] {rec['question']:24s} {rec['lang']} {rec['mode']:11s} "
                  f"{rec['arm']:16s} {rec['model']:28s} {rec['outcome']:12s} {shown}", flush=True)

    order = {q["id"]: i for i, q in enumerate(cfg.QUESTIONS)}
    records.sort(key=lambda r: (order[r["question"]], cfg.MODES.index(r["mode"]), r["lang"],
                                cfg.ALL_ARMS.index(r["arm"]), cfg.MODELS.index(r["model"]), r["run"]))
    with open(out_dir / "results.csv", "w", newline="") as f:
        fields = [k for k in records[0] if not k.startswith("_")]
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    write_report(records, fp_sets, meta, out_dir, args)

    print(f"\nTotal spend: {costing.fmt_usd(spend)} across {len(records)} episodes")
    print(f"Wrote {out_dir / 'results.csv'}\nWrote {out_dir / 'report.md'}\n"
          f"Wrote {len(records)} transcripts to {transcripts_dir}")


if __name__ == "__main__":
    main()
