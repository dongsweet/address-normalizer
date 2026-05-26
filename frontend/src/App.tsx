import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clipboard,
  Database,
  FileJson,
  List,
  Loader2,
  Play,
  RefreshCw,
  Save,
  Table2,
  Trash2
} from "lucide-react";
import { confirmResult, getConfigStatus, normalizeBatchStream } from "./api";
import type { ConfigStatus, NormalizedAddress, NormalizeStreamEvent, RowProgress } from "./types";

const SAMPLE_INPUT = [
  "友好北路689美美友好购物中心H&M，放前台",
  "美美购物中心一楼gucci，到了打电话",
  "玄武湖路999万达广场hm门口",
  "北京中路147号H&M 不用敲门",
  "长春北路668 hm，放门口",
  "友好北路689美美一层范思哲",
  "高铁站万达广场一楼",
  "友好步行街美美乌鲁木齐一层N-101B店铺",
  "南纬路街道北京中路147号1-2-1单元",
  "水磨沟区六道湾附近"
].join("\n");

type ViewMode = "table" | "json" | "lines";
const AUTO_PERSIST_WARNING = "已自动沉淀到记忆库";
const MANUAL_PERSIST_WARNING = "已手动沉淀到记忆库";

export function App() {
  const [input, setInput] = useState(SAMPLE_INPUT);
  const [results, setResults] = useState<NormalizedAddress[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [viewMode, setViewMode] = useState<ViewMode>("table");
  const [status, setStatus] = useState<ConfigStatus | null>(null);
  const [statusError, setStatusError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [useQwen, setUseQwen] = useState(true);
  const [useMapApi, setUseMapApi] = useState(true);
  const [autoPersistMemory, setAutoPersistMemory] = useState(true);
  const [concurrency, setConcurrency] = useState(2);
  const [progressRows, setProgressRows] = useState<RowProgress[]>([]);

  useEffect(() => {
    void refreshStatus();
  }, []);

  const addresses = useMemo(
    () => input.split(/\r?\n/).map((line) => line.trim()).filter(Boolean),
    [input]
  );
  const activeResults = progressRows.length
    ? progressRows.map((row) => row.result).filter((item): item is NormalizedAddress => Boolean(item))
    : results;
  const selected = progressRows.length ? progressRows[selectedIndex]?.result : results[selectedIndex];
  const progressDone = progressRows.filter((row) => row.status === "done" || row.status === "error").length;
  const progressSucceeded = progressRows.filter((row) => row.result && !isUnmatched(row.result)).length;
  const progressFailed = progressRows.filter((row) => isFailedRow(row)).length;
  const progressPersisted = progressRows.filter((row) => row.result && isPersisted(row.result)).length;
  const outputLines = activeResults.map(formatOutputLine).join("\n");
  const outputJson = JSON.stringify(activeResults, null, 2);

  async function refreshStatus() {
    try {
      setStatusError("");
      setStatus(await getConfigStatus());
    } catch (err) {
      setStatus(null);
      setStatusError(err instanceof Error ? err.message : "配置状态获取失败");
    }
  }

  async function runNormalize() {
    if (!addresses.length) return;
    setBusy(true);
    setError("");
    setResults([]);
    setSelectedIndex(0);
    setProgressRows(
      addresses.map((address, index) => ({
        index,
        input: address,
        status: "pending",
        stage: "pending",
        message: "等待中"
      }))
    );
    const streamedResults: NormalizedAddress[] = [];
    try {
      await normalizeBatchStream(addresses, useQwen, useMapApi, autoPersistMemory, concurrency, (event) => {
        handleStreamEvent(event, streamedResults);
      });
      setResults(streamedResults.filter(Boolean));
      await refreshStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "地址规范化失败");
    } finally {
      setBusy(false);
    }
  }

  function handleStreamEvent(event: NormalizeStreamEvent, streamedResults: NormalizedAddress[]) {
    if (event.type === "progress") {
      setProgressRows((current) =>
        current.map((row) =>
          row.index === event.index
            ? {
                ...row,
                status: "running",
                stage: event.stage,
                message: event.message,
                elapsed_ms: event.elapsed_ms
              }
            : row
        )
      );
      return;
    }

    if (event.type === "result") {
      streamedResults[event.index] = event.result;
      const resultStage = resultStageLabel(event.result, event.stage);
      setProgressRows((current) =>
        current.map((row) =>
          row.index === event.index
            ? {
                ...row,
                status: "done",
                stage: resultStage,
                message: `${event.result.source} · ${event.result.confidence.toFixed(3)}`,
                elapsed_ms: event.elapsed_ms,
                result: event.result
              }
            : row
        )
      );
      return;
    }

    if (event.type === "error") {
      setProgressRows((current) =>
        current.map((row) =>
          row.index === event.index
            ? {
                ...row,
                status: "error",
                stage: event.stage,
                message: event.message,
                elapsed_ms: event.elapsed_ms
              }
            : row
        )
      );
    }
  }

  async function copyOutput() {
    const text = viewMode === "json" ? outputJson : outputLines;
    await navigator.clipboard.writeText(text);
  }

  async function saveSelected() {
    if (!selected || isPersisted(selected)) return;
    setBusy(true);
    setError("");
    try {
      await confirmResult(selected);
      appendPersistWarning(selectedIndex, MANUAL_PERSIST_WARNING);
      await refreshStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "沉淀失败");
    } finally {
      setBusy(false);
    }
  }

  function appendPersistWarning(index: number, warning: string) {
    setProgressRows((current) =>
      current.map((row) =>
        row.index === index && row.result
          ? {
              ...row,
              result: appendWarning(row.result, warning)
            }
          : row
      )
    );
    setResults((current) =>
      current.map((result, currentIndex) => (currentIndex === index ? appendWarning(result, warning) : result))
    );
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <h1>地址规范化工作台</h1>
          <div className="subtle">乌鲁木齐 · POI锚定 · 标准库可选</div>
        </div>
        <div className="statusRow">
          <StatusPill label="PG" value={status?.database ?? "unknown"} />
          <StatusPill label="POI" value={status ? `${status.poi_rows}` : "0"} />
          <StatusPill label="记忆" value={status ? `${status.memory_rows}` : "0"} />
          <StatusPill label="别名" value={status ? `${status.memory_alias_rows}` : "0"} />
          <StatusPill label="明细" value={status ? `${status.memory_detail_rows}` : "0"} />
          <StatusPill label="标准库" value={status?.standard_address ?? "missing"} />
          <StatusPill label="Qwen" value={status?.qwen ?? "disabled"} />
          <StatusPill label="MGeo" value={status?.mgeo ?? "disabled"} />
          <StatusPill label="地图" value={status?.map_api ?? "disabled"} />
          <StatusPill label="今日地图" value={status ? `${status.map_api_calls_today}` : "0"} />
          <StatusPill label="今日Qwen" value={status ? `${status.qwen_calls_today}` : "0"} />
          <a className="textButton guideLink" href="/flow.html">
            <FileJson size={18} />
            <span>方案讲解</span>
          </a>
          <button className="iconButton" onClick={refreshStatus} title="刷新状态" aria-label="刷新状态">
            <RefreshCw size={18} />
          </button>
        </div>
      </header>

      {statusError && <div className="banner warn">{statusError}</div>}
      {error && <div className="banner error">{error}</div>}

      <section className="workspace">
        <section className="pane inputPane">
          <div className="paneHeader">
            <div className="paneTitle">
              <Database size={18} />
              <span>批量输入</span>
            </div>
            <span className="count">{addresses.length} 行</span>
          </div>
          <textarea value={input} onChange={(event) => setInput(event.target.value)} spellCheck={false} />
          <div className="toolbar">
            <button className="primaryButton" onClick={runNormalize} disabled={busy || !addresses.length}>
              {busy ? <Loader2 size={18} className="spin" /> : <Play size={18} />}
              <span>运行</span>
            </button>
            <button className="textButton" onClick={() => setInput(SAMPLE_INPUT)} disabled={busy}>
              <List size={18} />
              <span>样例</span>
            </button>
            <button className="iconButton" onClick={() => setInput("")} disabled={busy} title="清空" aria-label="清空">
              <Trash2 size={18} />
            </button>
            <label className="toggle">
              <input type="checkbox" checked={useQwen} onChange={(event) => setUseQwen(event.target.checked)} />
              <span>Qwen</span>
            </label>
            <label className="toggle">
              <input type="checkbox" checked={useMapApi} onChange={(event) => setUseMapApi(event.target.checked)} />
              <span>地图</span>
            </label>
            <label className="toggle" title="高质量命中自动写入记忆库">
              <input
                type="checkbox"
                checked={autoPersistMemory}
                onChange={(event) => setAutoPersistMemory(event.target.checked)}
                disabled={busy}
              />
              <span>自动沉淀</span>
            </label>
            <label className="selectControl">
              <span>并发</span>
              <select value={concurrency} onChange={(event) => setConcurrency(Number(event.target.value))} disabled={busy}>
                {[1, 2, 3, 4].map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </section>

        <section className="pane resultPane">
          <div className="paneHeader">
            <div className="tabs" role="tablist" aria-label="输出视图">
              <TabButton active={viewMode === "table"} onClick={() => setViewMode("table")} icon={<Table2 size={18} />} label="表格" />
              <TabButton active={viewMode === "json"} onClick={() => setViewMode("json")} icon={<FileJson size={18} />} label="JSON" />
              <TabButton active={viewMode === "lines"} onClick={() => setViewMode("lines")} icon={<List size={18} />} label="单行" />
            </div>
            <button className="iconButton" onClick={copyOutput} disabled={!activeResults.length} title="复制输出" aria-label="复制输出">
              <Clipboard size={18} />
            </button>
          </div>

          <div className="resultBody">
            <StageHelp />
            {progressRows.length > 0 && (
              <ProgressSummary
                total={progressRows.length}
                done={progressDone}
                succeeded={progressSucceeded}
                failed={progressFailed}
                persisted={progressPersisted}
                busy={busy}
              />
            )}
            {viewMode === "table" && (
              <ResultTable
                results={activeResults}
                progressRows={progressRows}
                selectedIndex={selectedIndex}
                onSelect={setSelectedIndex}
              />
            )}
            {viewMode === "json" && <pre className="codeBlock">{outputJson || "[]"}</pre>}
            {viewMode === "lines" && <pre className="codeBlock">{outputLines}</pre>}
          </div>
        </section>

        <aside className="pane detailPane">
          <div className="paneHeader">
            <div className="paneTitle">
              <FileJson size={18} />
              <span>详情</span>
            </div>
            <button className="textButton" onClick={saveSelected} disabled={!selected || isUnmatched(selected) || isPersisted(selected) || busy}>
              <Save size={18} />
              <span>{selected ? persistButtonLabel(selected) : "沉淀本条"}</span>
            </button>
          </div>
          {selected ? (
            <div className="detailContent">
              <div className={`detailAddress ${isUnmatched(selected) ? "unmatched" : ""}`}>
                {displayAddress(selected)}
              </div>
              <div className="metaGrid">
                <Meta label="最终来源" value={sourceLabel(selected.source)} />
                <Meta label="置信度" value={selected.confidence.toFixed(3)} />
                <Meta label="层级" value={selected.match_level} />
                <Meta label="沉淀" value={persistStateLabel(selected)} />
                <Meta label="锚点" value={selected.anchor_id ?? "-"} />
              </div>
              <pre className="codeBlock small">{JSON.stringify(selected, null, 2)}</pre>
            </div>
          ) : (
            <div className="empty">暂无结果</div>
          )}
        </aside>
      </section>
    </main>
  );
}

function resultStageLabel(result: NormalizedAddress, fallback: string) {
  if (result.anchor_type === "unmatched" || result.source === "none") {
    return "unmatched";
  }
  if (result.warnings.some((warning) => warning.includes("高置信"))) {
    return "fast_path";
  }
  return fallback;
}

function isUnmatched(result: NormalizedAddress) {
  return result.anchor_type === "unmatched" || result.source === "none";
}

function isPersisted(result: NormalizedAddress) {
  return isAutoPersisted(result) || result.warnings.includes(MANUAL_PERSIST_WARNING);
}

function isAutoPersisted(result: NormalizedAddress) {
  return result.warnings.includes(AUTO_PERSIST_WARNING);
}

function persistStateLabel(result: NormalizedAddress) {
  if (isAutoPersisted(result)) {
    return "自动";
  }
  if (result.warnings.includes(MANUAL_PERSIST_WARNING)) {
    return "手动";
  }
  return "未沉淀";
}

function persistButtonLabel(result: NormalizedAddress) {
  if (isAutoPersisted(result)) {
    return "已自动沉淀";
  }
  if (result.warnings.includes(MANUAL_PERSIST_WARNING)) {
    return "已手动沉淀";
  }
  return "沉淀本条";
}

function appendWarning(result: NormalizedAddress, warning: string): NormalizedAddress {
  if (result.warnings.includes(warning)) {
    return result;
  }
  return {
    ...result,
    warnings: [...result.warnings, warning]
  };
}

function isFailedRow(row: RowProgress) {
  return row.status === "error" || Boolean(row.result && isUnmatched(row.result));
}

function displayAddress(result: NormalizedAddress) {
  if (isUnmatched(result)) {
    return `未匹配：${result.cleaned_input || result.input}`;
  }
  return result.normalized_address;
}

function formatOutputLine(result: NormalizedAddress) {
  if (isUnmatched(result)) {
    return `[未匹配] ${result.cleaned_input || result.input}`;
  }
  return result.output_line;
}

function StageHelp() {
  const resultItems = [
    ["成功", "可信规范地址"],
    ["失败", "拒识或错误"],
    ["来源", "最终候选来源"]
  ];
  const items = [
    ["召回", "库内候选"],
    ["地图", "API补召回"],
    ["MGeo", "地址要素拆分"],
    ["Qwen", "候选择优/拒识"],
    ["直出", "高置信跳过模型"],
    ["拒识", "无可信地址"]
  ];
  return (
    <div className="stageHelp">
      <div className="stageHelpGroup">
        <span className="stageHelpTitle">结果说明</span>
        <div className="stageHelpItems">
          {resultItems.map(([label, description]) => (
            <span className="stageHelpItem" key={label}>
              <b>{label}</b>
              <span>{description}</span>
            </span>
          ))}
        </div>
      </div>
      <div className="stageHelpGroup">
        <span className="stageHelpTitle">阶段说明</span>
        <div className="stageHelpItems">
          {items.map(([label, description]) => (
            <span className="stageHelpItem" key={label}>
              <b>{label}</b>
              <span>{description}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

function ProgressSummary({
  total,
  done,
  succeeded,
  failed,
  persisted,
  busy
}: {
  total: number;
  done: number;
  succeeded: number;
  failed: number;
  persisted: number;
  busy: boolean;
}) {
  const percent = total ? Math.round((done / total) * 100) : 0;
  return (
    <div className="progressPanel">
      <div className="progressTop">
        <div className="paneTitle">
          {busy ? <Activity size={18} className="pulse" /> : <CheckCircle2 size={18} />}
          <span>{busy ? "处理中" : "处理完成"}</span>
        </div>
        <span className="count">
          {done}/{total}
          {done ? ` · 成功 ${succeeded}` : ""}
          {failed ? ` · 失败 ${failed}` : ""}
          {persisted ? ` · 沉淀 ${persisted}` : ""}
        </span>
      </div>
      <div className="progressTrack" aria-label="整体进度">
        <div className="progressFill" style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

function StatusPill({ label, value }: { label: string; value: string }) {
  const good = ["configured", "ok"].includes(value) || /^\d+$/.test(value);
  const missing = ["missing", "disabled", "unavailable", "unknown"].includes(value);
  const displayValue = statusDisplayValue(value);
  return (
    <span className={`statusPill ${good ? "good" : ""} ${missing ? "muted" : ""}`} title={`${label}: ${value}`}>
      <b>{label}</b>
      <span>{displayValue}</span>
    </span>
  );
}

function statusDisplayValue(value: string) {
  const labels: Record<string, string> = {
    configured: "已配",
    disabled: "关闭",
    missing: "缺失",
    unavailable: "不可用",
    unknown: "未知",
    ok: "正常"
  };
  return labels[value] ?? value;
}

function TabButton({
  active,
  onClick,
  icon,
  label
}: {
  active: boolean;
  onClick: () => void;
  icon: ReactNode;
  label: string;
}) {
  return (
    <button className={`tabButton ${active ? "active" : ""}`} onClick={onClick}>
      {icon}
      <span>{label}</span>
    </button>
  );
}

function ResultTable({
  results,
  progressRows,
  selectedIndex,
  onSelect
}: {
  results: NormalizedAddress[];
  progressRows: RowProgress[];
  selectedIndex: number;
  onSelect: (index: number) => void;
}) {
  if (!results.length && !progressRows.length) {
    return <div className="empty">暂无结果</div>;
  }
  const rows = progressRows.length ? progressRows : results.map((result, index) => ({
    index,
    input: result.input,
    status: "done" as const,
    stage: "done",
    message: `${result.source} · ${result.confidence.toFixed(3)}`,
    elapsed_ms: undefined,
    result
  }));
  return (
    <div className="tableWrap">
      <table>
        <thead>
          <tr>
            <th>原始地址</th>
            <th>规范结果</th>
            <th>最终结果</th>
            <th>阶段</th>
            <th>最终来源</th>
            <th>置信度</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.input}-${index}`} className={index === selectedIndex ? "selected" : ""} onClick={() => onSelect(index)}>
              <td>{row.input}</td>
              <td>
                {row.result ? (
                  <span className={isUnmatched(row.result) ? "unmatchedText" : ""}>
                    {displayAddress(row.result)}
                  </span>
                ) : (
                  row.message
                )}
              </td>
              <td>
                <span className={`resultBadge ${resultStatusClass(row)}`}>
                  {resultStatusLabel(row)}
                </span>
              </td>
              <td>
                <span className={`progressBadge ${stageBadgeClass(row)}`}>
                  {row.status === "error" && <AlertTriangle size={14} />}
                  {stageLabel(row.stage)}
                </span>
              </td>
              <td>
                <span className={`sourceBadge ${rowSourceClass(row)}`}>
                  {rowSourceLabel(row)}
                </span>
              </td>
              <td>{row.result ? row.result.confidence.toFixed(3) : row.elapsed_ms ? `${(row.elapsed_ms / 1000).toFixed(1)}s` : "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function stageLabel(stage: string) {
  const labels: Record<string, string> = {
    pending: "等待",
    start: "开始",
    clean: "清洗",
    recall: "召回",
    rank: "排序",
    map_api: "地图",
    mgeo: "MGeo",
    qwen: "Qwen",
    fast_path: "直出",
    unmatched: "拒识",
    done: "完成",
    error: "错误"
  };
  return labels[stage] ?? stage;
}

function stageBadgeClass(row: RowProgress) {
  if (isFailedRow(row)) {
    return "error";
  }
  return row.status;
}

function resultStatusLabel(row: RowProgress) {
  if (row.result) {
    return isUnmatched(row.result) ? "失败" : "成功";
  }
  if (row.status === "error") {
    return "失败";
  }
  if (row.status === "pending") {
    return "等待";
  }
  return "处理中";
}

function resultStatusClass(row: RowProgress) {
  if (row.result) {
    return isUnmatched(row.result) ? "failed" : "success";
  }
  if (row.status === "error") {
    return "failed";
  }
  if (row.status === "pending") {
    return "pending";
  }
  return "running";
}

function sourceLabel(source?: string) {
  const labels: Record<string, string> = {
    map_api: "地图API",
    poi: "POI快照",
    memory: "记忆库",
    standard: "标准库",
    qwen: "Qwen",
    none: "无"
  };
  return labels[source ?? "none"] ?? source ?? "无";
}

function sourceClass(source?: string) {
  if (!source || source === "none") {
    return "none";
  }
  return source;
}

function rowSourceLabel(row: RowProgress) {
  if (row.result) {
    return sourceLabel(row.result.source);
  }
  if (row.status === "pending" || row.status === "running") {
    return "待定";
  }
  return "无";
}

function rowSourceClass(row: RowProgress) {
  if (row.result) {
    return sourceClass(row.result.source);
  }
  if (row.status === "pending" || row.status === "running") {
    return "pending";
  }
  return "none";
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="metaItem">
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}
