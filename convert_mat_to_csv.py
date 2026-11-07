#!/usr/bin/env python
"""
convert_mat_to_csv.py — 把 data_GeorgiaTech/ 里的 MATLAB .mat 全量转成 CSV。

背景
----
`data_GeorgiaTech/` 是 Georgia Tech 下肢生物力学多模态数据集 (Camargo et al. 2021)，
每个 trial 是一个 MATLAB `table`，存在 v5 MAT-file 里 —— 而且是被序列化成 MCOS
opaque 对象的。实测：

  * ``scipy.io.loadmat``  → 只拿到 ``MatlabOpaque`` 占位符，**拿不到任何数据**
  * ``pymatreader``       → 同样只有占位符，真实数据被原样丢进 ``__function_workspace__``
  * MATLAB R2024a        → ✅ ``load`` 出来就是真 table

所以解码必须借助 MATLAB。本脚本分两段：

  ① MATLAB 阶段 (matlab/decode_subject.m)
     每个 subject 跑一次 ``matlab -batch``，把 ``.mat``(table) 拆成中间态
     ``.mat``：纯 double 矩阵 + 字符串 cell + 列名，scipy 能直接读。

  ② Python 阶段 (本文件)
     读中间态 → 拼 DataFrame → ``df.to_csv()`` 写最终 CSV。
     最终产物由 pandas 生成，因此行尾 (CRLF) 与 float 文本表示与仓库根目录
     那套参考镜像 (levelground/ ramp/ stair/ treadmill/，其实是 AB06 的镜像)
     **逐字节一致**；emg 传感器还会顺带算出 ISEK 包络 emg_envelope/。

输出结构
--------
    data_GeorgiaTech_csv/<SUBJ>/<condition>/<sensor>/<trial>.csv

例如::

    data_GeorgiaTech_csv/AB06/levelground/emg/levelground_ccw_fast_01_01.csv
    data_GeorgiaTech_csv/AB06/levelground/emg_envelope/levelground_ccw_fast_01_01.csv

用法
----
    uv run python convert_mat_to_csv.py                 # 全量（21 个 subject）
    uv run python convert_mat_to_csv.py --subject AB06   # 只转某个 subject
    uv run python convert_mat_to_csv.py --sensors emg imu
    uv run python convert_mat_to_csv.py --dry-run        # 只列清单不转换
    uv run python convert_mat_to_csv.py --verify         # 只校验已有产物
    uv run python convert_mat_to_csv.py --force          # 覆盖已存在的 CSV

断点续跑：目标 CSV 已存在就跳过（除非 ``--force``）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.io
from scipy.signal import butter, filtfilt

# ── 常量 ─────────────────────────────────────────────────────────────────────

REPO = Path(__file__).resolve().parent
SRC_ROOT = REPO / "data_GeorgiaTech"
DST_ROOT = REPO / "data_GeorgiaTech_csv"
MATLAB_DIR = REPO / "matlab"
REPORT_DIR = DST_ROOT / "_conversion_report"
WORK_ROOT = DST_ROOT / "_work"

#: 只转这 4 个运动条件。数据集里还有第 5 个 `static/`（只有 ik/ik_offset/markers，
#: 根目录参考镜像里也没有），本次不转。
CONDITIONS = ("levelground", "ramp", "stair", "treadmill")

#: 12 类传感器，顺序即输出目录里的顺序。
SENSORS = (
    "conditions", "emg", "fp", "gcLeft", "gcRight", "gon",
    "id", "ik", "ik_offset", "imu", "jp", "markers",
)

#: 需要额外算 ISEK 包络的传感器。
ENVELOPE_SENSOR = "emg"
ENVELOPE_DIR = "emg_envelope"

#: 包络参数：整流 → Butterworth 低通 → filtfilt 零相位。
#: 反推自根目录 136 个参考包络文件，最佳拟合 mean corr = 0.9973。
ENVELOPE_CUTOFF_HZ = 6.0
ENVELOPE_ORDER = 4

#: 每个 subject 跑完后打印一次进度。
LOG_EVERY = 200


# ── 任务描述 ─────────────────────────────────────────────────────────────────

@dataclass
class Job:
    """一个 .mat → .csv 的转换任务。"""

    subject: str
    condition: str
    sensor: str
    trial: str          # 小写化的文件名（不含扩展名）
    src: Path           # 源 .mat
    dst: Path           # 目标 .csv
    work: Path          # 中间态 .mat


# ── 工具函数 ─────────────────────────────────────────────────────────────────

def _mstr(path: Path) -> str:
    """转成 MATLAB 能接受的路径写法（正斜杠）。"""
    return str(path).replace("\\", "/")


def find_matlab(explicit: str | None = None) -> Path | None:
    """定位 MATLAB 可执行文件。"""
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None

    env = os.environ.get("MATLAB_EXE")
    if env and Path(env).exists():
        return Path(env)

    which = shutil.which("matlab")
    if which:
        return Path(which)

    for base in (Path("C:/Program Files/MATLAB"), Path("D:/Program Files/MATLAB")):
        if not base.is_dir():
            continue
        cands = sorted(base.glob("*/bin/matlab.exe"), reverse=True)
        if cands:
            return cands[0]
    return None


def discover_subjects() -> list[str]:
    """列出所有受试者目录名（AB06 …）。"""
    if not SRC_ROOT.is_dir():
        return []
    return sorted(p.name for p in SRC_ROOT.iterdir()
                  if p.is_dir() and p.name.upper().startswith("AB"))


def discover_jobs(subject: str, sensors, conditions) -> list[Job]:
    """遍历一个 subject 的所有 .mat，生成任务列表。

    目录结构：``<subject>/<date>/<condition>/<sensor>/<file>.mat``。
    每个 subject 只有一个日期目录，输出里不带日期层。
    """
    jobs: list[Job] = []
    seen: dict[Path, Job] = {}
    subj_dir = SRC_ROOT / subject
    if not subj_dir.is_dir():
        return jobs

    for date_dir in sorted(p for p in subj_dir.iterdir()
                           if p.is_dir() and p.name != "osimxml"):
        for condition in conditions:
            cond_dir = date_dir / condition
            if not cond_dir.is_dir():
                continue
            for sensor in sensors:
                sensor_dir = cond_dir / sensor
                if not sensor_dir.is_dir():
                    continue
                for mat in sorted(sensor_dir.glob("*.mat")):
                    trial = mat.stem.lower()          # 统一小写
                    dst = DST_ROOT / subject / condition / sensor / f"{trial}.csv"
                    if dst in seen:                    # 同名冲突（理论上没有）
                        continue
                    work = WORK_ROOT / subject / sensor / f"{trial}.mat"
                    job = Job(subject, condition, sensor, trial, mat, dst, work)
                    seen[dst] = job
                    jobs.append(job)
    return jobs


def _cell_to_list(cell) -> list[str]:
    """把 MATLAB cell（char 矩阵）转成 python str 列表。"""
    arr = np.asarray(cell)
    out: list[str] = []
    for item in arr.ravel():
        if isinstance(item, np.ndarray):
            if item.dtype.kind == "U":
                out.append("".join(str(x) for x in item.ravel()))
            else:
                out.append("".join(chr(c) for c in np.asarray(item).ravel()))
        elif isinstance(item, str):
            out.append(item)
        else:
            out.append("")
    return out


def intermediate_to_frame(path: Path) -> pd.DataFrame:
    """读中间态 .mat → DataFrame（列名与列顺序跟原 table 一致）。"""
    m = scipy.io.loadmat(path, squeeze_me=False, struct_as_record=False)

    cols = _cell_to_list(m["cols"])
    num = np.asarray(m["num_data"], dtype=float)
    is_str = np.asarray(m["is_str"]).ravel().astype(bool)

    if num.ndim == 1:                      # 单列时被 squeeze 的兜底
        num = num.reshape(-1, 1)
    if num.shape[1] != len(cols):          # 理论上不会发生，兜底
        num = num.reshape(num.shape[0], -1)

    df = pd.DataFrame(num, columns=cols)

    if is_str.any() and "str_data" in m:
        strings = _cell_to_list(m["str_data"])
        n = len(df)
        k = int(is_str.sum())
        blocks = [strings[i * n:(i + 1) * n] for i in range(k)]
        b = 0
        for j, col in enumerate(cols):
            if is_str[j]:
                df[col] = pd.Series(blocks[b])
                b += 1
    return df


def compute_envelope(df: pd.DataFrame) -> pd.DataFrame:
    """ISEP/ISEK 包络：整流 → Butterworth 低通 (fc=6Hz, 4 阶) → 零相位。

    采样率从 Header 时间戳的中位间隔实算，不写死。
    """
    out = df.copy()
    if "Header" not in df.columns or len(df) < 3:
        return out

    t = df["Header"].to_numpy(dtype=float)
    dt = float(np.median(np.diff(t)))
    fs = 1.0 / dt if dt > 0 else 1000.0
    wn = min(ENVELOPE_CUTOFF_HZ / (fs / 2.0), 0.99)

    b, a = butter(ENVELOPE_ORDER, wn, btype="low")
    for col in df.columns:
        if col == "Header":
            continue
        x = df[col].to_numpy(dtype=float)
        if len(x) <= 3 * max(len(a), len(b)):
            out[col] = np.abs(x)
        else:
            out[col] = filtfilt(b, a, np.abs(x))
    return out


def write_csv(df: pd.DataFrame, path: Path) -> None:
    """写最终 CSV（pandas 默认 → Windows 下 CRLF + 最短往返 float repr）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


# ── MATLAB 阶段 ──────────────────────────────────────────────────────────────

def run_matlab_decode(matlab: Path, jobs: list[Job], work_dir: Path) -> dict:
    """调用 MATLAB 把一批 .mat(table) 解成中间态 .mat。

    返回 ``{dst路径: (ok, rows, cols, error)}``。
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    manifest = work_dir / "manifest.json"
    result_file = work_dir / "result.json"
    if result_file.exists():
        result_file.unlink()

    manifest.write_text(json.dumps({
        "src": [str(j.src) for j in jobs],
        "dst": [str(j.work) for j in jobs],
    }), encoding="utf-8")

    cmd = [
        str(matlab), "-batch",
        f"addpath('{_mstr(MATLAB_DIR)}'); "
        f"decode_subject('{_mstr(manifest)}','{_mstr(result_file)}');",
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True,
                          errors="replace", encoding="utf-8")

    outcomes: dict[str, tuple[bool, int, int, str]] = {}
    if result_file.exists():
        try:
            payload = json.loads(result_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {"results": []}
        for item in payload.get("results", []):
            outcomes[item["dst"]] = (
                bool(item.get("ok")),
                int(item.get("rows", 0)),
                int(item.get("cols", 0)),
                item.get("error", "") or "",
            )
    else:
        # MATLAB 整个挂掉：把 stderr 塞进每个任务的 error 里
        tail = (proc.stderr or proc.stdout or "MATLAB failed")[-2000:]
        for job in jobs:
            outcomes[str(job.work)] = (False, 0, 0, tail)

    return {
        "outcomes": outcomes,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-4000:],
        "stderr": (proc.stderr or "")[-4000:],
    }


# ── 单个 subject 的完整流程 ─────────────────────────────────────────────────

def convert_subject(subject: str, jobs: list[Job], args) -> dict:
    """转一个 subject 的所有 job，返回统计结果。"""
    t0 = time.time()
    stats = {
        "subject": subject,
        "jobs": len(jobs),
        "written": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
    }
    if not jobs:
        return stats

    pending = jobs if args.force else [j for j in jobs if not j.dst.exists()]
    stats["skipped"] = len(jobs) - len(pending)

    if not pending:
        print(f"[{subject}] 全部已存在，跳过 ({len(jobs)} 个)")
        stats["seconds"] = round(time.time() - t0, 1)
        return stats

    work_dir = WORK_ROOT / subject
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)

    print(f"[{subject}] 待转换 {len(pending)} / 共 {len(jobs)} 个 "
          f"(MATLAB 解码中 …)", flush=True)

    matlab = find_matlab(args.matlab)
    if matlab is None:
        stats["failed"] = len(pending)
        stats["errors"].append({
            "file": "*", "stage": "matlab",
            "error": "找不到 MATLAB 可执行文件；请用 --matlab 指定路径，"
                     "或设置环境变量 MATLAB_EXE",
        })
        return stats

    mat_res = run_matlab_decode(matlab, pending, work_dir)
    outcomes = mat_res["outcomes"]

    n = 0
    for job in pending:
        n += 1
        ok, rows, cols, err = outcomes.get(str(job.work), (False, 0, 0, "no result"))
        if not ok:
            stats["failed"] += 1
            stats["errors"].append({
                "file": str(job.src), "stage": "matlab", "error": err,
            })
            continue
        try:
            df = intermediate_to_frame(job.work)
            if len(df.columns) != cols or len(df) != rows:
                raise ValueError(
                    f"形状不符: 期望 {rows}x{cols}, 实际 {len(df)}x{len(df.columns)}"
                )
            if len(df.columns) == 0 or df.columns[0] != "Header":
                raise ValueError(f"首列不是 Header: {list(df.columns[:3])}")
            write_csv(df, job.dst)

            if (not args.no_envelope
                    and job.sensor == ENVELOPE_SENSOR
                    and job.condition in CONDITIONS):
                env_dst = (job.dst.parent.parent / ENVELOPE_DIR / job.dst.name)
                write_csv(compute_envelope(df), env_dst)

            stats["written"] += 1
        except Exception as exc:  # noqa: BLE001
            stats["failed"] += 1
            stats["errors"].append({
                "file": str(job.src), "stage": "python", "error": repr(exc),
            })
        finally:
            job.work.unlink(missing_ok=True)

        if n % LOG_EVERY == 0 or n == len(pending):
            print(f"  [{subject}] {n}/{len(pending)}  "
                  f"成功 {stats['written']} 失败 {stats['failed']}", flush=True)

    if mat_res["returncode"] != 0 and not mat_res["outcomes"]:
        stats["errors"].append({
            "file": "*", "stage": "matlab",
            "error": f"matlab 退出码 {mat_res['returncode']}\n"
                     f"{mat_res['stderr'][-1500:]}",
        })

    shutil.rmtree(work_dir, ignore_errors=True)
    stats["seconds"] = round(time.time() - t0, 1)
    print(f"[{subject}] 完成：写入 {stats['written']}，跳过 {stats['skipped']}，"
          f"失败 {stats['failed']}，耗时 {stats['seconds']}s", flush=True)
    return stats


# ── 校验 ─────────────────────────────────────────────────────────────────────

def verify(args) -> int:
    """检查目标 CSV 是否存在、表头是否以 Header 开头、列数是否符合预期。"""
    subjects = args.subject or discover_subjects()
    bad = 0
    total = 0
    for subject in subjects:
        jobs = discover_jobs(subject, args.sensors, args.conditions)
        for job in jobs:
            total += 1
            if not job.dst.exists():
                print(f"缺失: {job.dst}")
                bad += 1
                continue
            try:
                with open(job.dst, "r", encoding="utf-8") as fh:
                    header = fh.readline().rstrip("\r\n").split(",")
                if not header or header[0] != "Header":
                    print(f"表头异常: {job.dst}")
                    bad += 1
            except Exception as exc:  # noqa: BLE001
                print(f"读取失败: {job.dst} ({exc})")
                bad += 1
    print(f"\n校验完成：{total} 个文件，异常 {bad} 个")
    return 1 if bad else 0


# ── 入口 ─────────────────────────────────────────────────────────────────────

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="把 data_GeorgiaTech/ 的 MATLAB .mat 全量转成 CSV",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--subject", action="append",
                   help="只处理指定 subject（可重复）；默认全部")
    p.add_argument("--sensors", nargs="+", default=list(SENSORS),
                   choices=list(SENSORS), help="限定传感器")
    p.add_argument("--conditions", nargs="+", default=list(CONDITIONS),
                   choices=list(CONDITIONS), help="限定运动条件")
    p.add_argument("--force", action="store_true", help="覆盖已存在的 CSV")
    p.add_argument("--no-envelope", action="store_true", help="不算 emg_envelope")
    p.add_argument("--verify", action="store_true", help="只校验，不转换")
    p.add_argument("--dry-run", action="store_true", help="只列清单")
    p.add_argument("--matlab", help="MATLAB 可执行文件路径")
    p.add_argument("--report-dir", default=str(REPORT_DIR), help="报告输出目录")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    # 控制台编码可能是 GBK，遇到文件名里无法编码的字符时不要把脚本炸掉
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    if not SRC_ROOT.is_dir():
        print(f"找不到源数据目录: {SRC_ROOT}", file=sys.stderr)
        return 2

    if args.verify:
        return verify(args)

    subjects = args.subject or discover_subjects()
    if not subjects:
        print(f"{SRC_ROOT} 下没有找到 subject 目录", file=sys.stderr)
        return 2

    print(f"源目录 : {SRC_ROOT}")
    print(f"输出目录: {DST_ROOT}")
    print(f"subject: {len(subjects)} 个 -> {', '.join(subjects)}")
    print()

    all_stats = []
    total_jobs = 0
    for subject in subjects:
        jobs = discover_jobs(subject, args.sensors, args.conditions)
        total_jobs += len(jobs)
        if args.dry_run:
            by_sensor: dict[str, int] = {}
            for job in jobs:
                by_sensor[job.sensor] = by_sensor.get(job.sensor, 0) + 1
            print(f"[{subject}] {len(jobs)} 个文件  {by_sensor}")
            continue
        all_stats.append(convert_subject(subject, jobs, args))

    if args.dry_run:
        print(f"\n合计 {total_jobs} 个文件（未转换）")
        return 0

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    for st in all_stats:
        (report_dir / f"{st['subject']}.json").write_text(
            json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")

    written = sum(s["written"] for s in all_stats)
    skipped = sum(s["skipped"] for s in all_stats)
    failed = sum(s["failed"] for s in all_stats)
    print()
    print("=" * 60)
    print(f"全部完成：写入 {written}，跳过 {skipped}，失败 {failed}")
    if failed:
        print(f"详见报告: {report_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
