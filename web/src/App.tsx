import { useEffect, useMemo, useState } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([LineChart, BarChart, GridComponent, TooltipComponent, CanvasRenderer]);

type ProviderInput = {
  name: string;
  base_url: string;
  api_key: string;
  model: string;
};
type Plugin = {
  name: string;
  description: string;
  config_schema: { properties?: Record<string, { default?: unknown; title?: string }> };
};
type ProviderSummary = { model: string; score: number | null; verdict: string | null };
type Run = {
  run_id: string;
  status: string;
  started_at?: string;
  providers?: Record<string, ProviderSummary>;
};
type RuntimeEvent = {
  type: string;
  status?: string;
  plugin?: string;
  provider?: string;
  error?: string;
  e2e_ms?: number;
  metrics?: Record<string, unknown>;
  progress?: number;
};
type PluginResult = { status: string; request_count: number; metrics: Record<string, unknown>; error?: string };
type ProviderResult = { model: string; scorecard: { score: number; verdict: string }; plugins: Record<string, PluginResult> };
type ComparisonMetric = { label: string; reference: number; candidate: number; delta: number; candidate_better: boolean };
type RunResult = Omit<Run, "providers"> & {
  providers: Record<string, ProviderResult>;
  comparisons: {
    reference?: string;
    candidates?: Record<string, { metrics: ComparisonMetric[]; identity?: Record<string, unknown> }>;
  };
};
type RawRecord = {
  request_id: string;
  provider: string;
  case_id: string;
  status: string;
  status_code?: number;
  e2e_ms: number;
  ttft_ms?: number;
  error?: string;
  request: Record<string, unknown>;
  response: Record<string, unknown>;
};

const blankProvider = (name: string): ProviderInput => ({
  name,
  base_url: "https://api.example.com/v1",
  api_key: "",
  model: "",
});

function Status({ value }: { value: string }) {
  return <span className={`status ${value}`}>{value}</span>;
}

function LiveChart({ events }: { events: RuntimeEvent[] }) {
  const points = useMemo(
    () => events.filter((event) => event.type === "request.completed"),
    [events],
  );
  useEffect(() => {
    const node = document.getElementById("live-chart");
    if (!node) return;
    const chart = echarts.init(node);
    chart.setOption({
      animationDuration: 250,
      grid: { left: 46, right: 18, top: 24, bottom: 32 },
      xAxis: { type: "category", data: points.map((_, index) => index + 1) },
      yAxis: { type: "value", name: "ms" },
      tooltip: { trigger: "axis" },
      series: [{ type: "line", smooth: true, showSymbol: false, data: points.map((event) => event.e2e_ms) }],
    });
    return () => chart.dispose();
  }, [points]);
  return <div id="live-chart" className="chart" />;
}

function ScoreChart({ providers }: { providers: Record<string, ProviderResult> }) {
  useEffect(() => {
    const node = document.getElementById("score-chart");
    if (!node) return;
    const chart = echarts.init(node);
    const entries = Object.entries(providers);
    chart.setOption({
      grid: { left: 42, right: 18, top: 20, bottom: 38 },
      xAxis: { type: "category", data: entries.map(([name]) => name) },
      yAxis: { type: "value", min: 0, max: 100 },
      tooltip: { trigger: "axis" },
      series: [{ type: "bar", data: entries.map(([, provider]) => provider.scorecard.score), itemStyle: { color: "#27814e", borderRadius: [5, 5, 0, 0] } }],
    });
    return () => chart.dispose();
  }, [providers]);
  return <div id="score-chart" className="score-chart" />;
}

function defaultSettings(plugin: Plugin): string {
  const defaults = Object.fromEntries(
    Object.entries(plugin.config_schema.properties || {})
      .filter(([key, value]) => key !== "enabled" && value.default !== undefined)
      .map(([key, value]) => [key, value.default]),
  );
  return JSON.stringify(defaults, null, 2);
}

function ResultPanel({ result, records, selectedProvider, onSelectProvider, onRerun }: { result: RunResult; records: RawRecord[]; selectedProvider?: string; onSelectProvider: (provider: string) => void; onRerun: (plugin: string) => void }) {
  return (
    <section className="panel results">
      <div className="panel-title"><span>04</span><div><h3>Result</h3><p>{result.run_id}</p></div><Status value={result.status} /></div>
      <div className="score-grid">
        {Object.entries(result.providers).map(([name, provider]) => (
          <article className="score-card" key={name}><small>{name} · {provider.model}</small><strong>{provider.scorecard.score.toFixed(2)}</strong><Status value={provider.scorecard.verdict} /></article>
        ))}
      </div>
      <ScoreChart providers={result.providers} />
      {result.comparisons?.reference && Object.entries(result.comparisons.candidates || {}).map(([name, comparison]) => (
        <div className="comparison" key={name}>
          <h4>{name} vs {result.comparisons.reference}</h4>
          <table><thead><tr><th>Metric</th><th>Reference</th><th>Candidate</th><th>Delta</th></tr></thead><tbody>
            {comparison.metrics.map((metric) => <tr key={metric.label}><td>{metric.label}</td><td>{metric.reference.toFixed(3)}</td><td>{metric.candidate.toFixed(3)}</td><td className={metric.candidate_better ? "positive" : "negative"}>{metric.delta > 0 ? "+" : ""}{metric.delta.toFixed(3)}</td></tr>)}
          </tbody></table>
          {comparison.identity && <pre className="identity">{JSON.stringify(comparison.identity, null, 2)}</pre>}
        </div>
      ))}
      <h4>Plugin outcomes</h4>
      <div className="plugin-results">
        {Object.entries(result.providers).flatMap(([providerName, provider]) => Object.entries(provider.plugins).map(([name, plugin]) => (
          <details key={`${providerName}-${name}`}><summary><b>{providerName}</b> / {name} <Status value={plugin.status} /> <small>{plugin.request_count} requests</small></summary><button className="text-button rerun" onClick={() => onRerun(name)}>Rerun this plugin ↻</button>{plugin.error && <div className="error">{plugin.error}</div>}<pre>{JSON.stringify(plugin.metrics, null, 2)}</pre></details>
        )))}
      </div>
      <div className="raw-title"><h4>Raw requests</h4><div>{Object.keys(result.providers).map((provider) => <button key={provider} className={`text-button ${provider === selectedProvider ? "selected-provider" : ""}`} onClick={() => onSelectProvider(provider)}>{provider}</button>)}<span>{records.length} loaded</span></div></div>
      <div className="raw-list">
        {records.map((record) => <details key={record.request_id} className={record.status === "success" ? "" : "raw-error"}><summary><Status value={record.status.toUpperCase()} /><code>{record.case_id}</code><span>{record.e2e_ms.toFixed(1)} ms</span></summary><div className="raw-columns"><div><b>Request</b><pre>{JSON.stringify(record.request, null, 2)}</pre></div><div><b>Response</b><pre>{JSON.stringify(record.response, null, 2)}</pre>{record.error && <div className="error">{record.error}</div>}</div></div></details>)}
        {!records.length && <div className="empty">No request records in this run.</div>}
      </div>
    </section>
  );
}

export function App() {
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState<string[]>(["compatibility", "latency"]);
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [scoring, setScoring] = useState('{\n  "gates": []\n}');
  const [providers, setProviders] = useState<ProviderInput[]>([blankProvider("official"), blankProvider("candidate")]);
  const [connection, setConnection] = useState<Record<number, string>>({});
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [pluginStates, setPluginStates] = useState<Record<string, string>>({});
  const [activeRun, setActiveRun] = useState<string>();
  const [progress, setProgress] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [result, setResult] = useState<RunResult>();
  const [records, setRecords] = useState<RawRecord[]>([]);
  const [rawProvider, setRawProvider] = useState<string>();

  const refreshRuns = () => fetch("/api/runs").then((response) => response.json()).then(setRuns);
  useEffect(() => {
    fetch("/api/plugins").then((response) => response.json()).then((items: Plugin[]) => {
      setPlugins(items);
      setSettings(Object.fromEntries(items.map((plugin) => [plugin.name, defaultSettings(plugin)])));
    });
    refreshRuns();
  }, []);

  const updateProvider = (index: number, key: keyof ProviderInput, value: string) => setProviders((current) => current.map((provider, item) => item === index ? { ...provider, [key]: value } : provider));

  const testConnection = async (index: number) => {
    setConnection((current) => ({ ...current, [index]: "TESTING" }));
    try {
      const response = await fetch("/api/providers/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(providers[index]) });
      const data = await response.json();
      setConnection((current) => ({ ...current, [index]: response.ok && data.connected ? `CONNECTED · ${data.latency_ms.toFixed(0)} ms` : `FAILED · ${data.error || data.chat_status}` }));
    } catch (reason) {
      setConnection((current) => ({ ...current, [index]: `FAILED · ${String(reason)}` }));
    }
  };

  const loadRecords = async (runId: string, provider: string, providerCount: number) => {
    const query = providerCount === 1 ? "?limit=500" : `?provider=${encodeURIComponent(provider)}&limit=500`;
    const raw = await fetch(`/api/runs/${runId}/requests${query}`).then((item) => item.json());
    setRawProvider(provider); setRecords(raw.items || []);
  };

  const loadResult = async (runId: string) => {
    const response = await fetch(`/api/runs/${runId}`);
    const data = await response.json() as RunResult;
    if (!data.providers || !Object.values(data.providers)[0]?.plugins) return;
    setResult(data);
    const providerNames = Object.keys(data.providers);
    await loadRecords(runId, providerNames[0], providerNames.length);
  };

  const observeRun = (runId: string) => {
    setActiveRun(runId);
    const stream = new EventSource(`/api/runs/${runId}/events`);
    stream.onmessage = (message) => {
      const event = JSON.parse(message.data) as RuntimeEvent;
      setEvents((current) => [...current, event]);
      if (event.progress !== undefined) setProgress(event.progress);
      if (event.provider && event.plugin && (event.type === "plugin.started" || event.type === "plugin.completed")) setPluginStates((current) => ({ ...current, [`${event.provider}/${event.plugin}`]: event.status || "RUNNING" }));
      if (event.type === "run.completed" || event.type === "run.failed") {
        stream.close(); setBusy(false); setProgress(1); refreshRuns(); loadResult(runId);
      }
    };
    stream.onerror = () => { stream.close(); setBusy(false); };
  };

  const start = async () => {
    setBusy(true); setError(undefined); setEvents([]); setProgress(0); setResult(undefined); setRecords([]);
    try {
      const benchmarks = Object.fromEntries(plugins.map((plugin) => {
        const parsed = settings[plugin.name]?.trim() ? JSON.parse(settings[plugin.name]) : {};
        return [plugin.name, { ...parsed, enabled: selected.includes(plugin.name) }];
      }));
      const configured = providers.filter((provider) => provider.name && provider.base_url && provider.api_key && provider.model);
      if (!configured.length) throw new Error("Configure at least one complete provider.");
      const scoringConfig = scoring.trim() ? JSON.parse(scoring) : {};
      const payload = configured.length === 1 ? { provider: configured[0], benchmarks, scoring: scoringConfig } : { providers: configured, benchmarks, scoring: scoringConfig, reference_provider: configured[0].name };
      const response = await fetch("/api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!response.ok) throw new Error(await response.text());
      const run = await response.json();
      setPluginStates(Object.fromEntries(configured.flatMap((provider) => plugins.map((plugin) => [`${provider.name}/${plugin.name}`, selected.includes(plugin.name) ? "PENDING" : "DISABLED"]))));
      observeRun(run.run_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason)); setBusy(false);
    }
  };

  const rerunPlugin = async (plugin: string) => {
    if (!result) return;
    setBusy(true); setError(undefined); setEvents([]); setProgress(0); setPluginStates({ [plugin]: "PENDING" });
    const response = await fetch(`/api/runs/${result.run_id}/rerun`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ plugins: [plugin] }) });
    if (!response.ok) { setError(await response.text()); setBusy(false); return; }
    const run = await response.json();
    observeRun(run.run_id);
  };

  const latestStatus = busy ? "RUNNING" : result?.status || "PENDING";
  const latestMetrics = [...events].reverse().find((event) => event.metrics)?.metrics;
  return (
    <div className="shell">
      <header><div className="mark">PB</div><div><h1>Provider Bench</h1><p>LLM API provider qualification console</p></div></header>
      <main>
        <section className="hero"><div><span className="eyebrow">BENCHMARK CONTROL</span><h2>Measure the channel,<br />not the claims.</h2></div><p>Run repeatable compatibility, quality and performance tests against OpenAI-compatible endpoints. Every request remains inspectable.</p></section>
        <div className="columns">
          <section className="panel">
            <div className="panel-title"><span>01</span><div><h3>New benchmark</h3><p>Compare one or more providers.</p></div></div>
            {providers.map((provider, index) => <div className="provider-form" key={index}>
              <div className="provider-heading"><h4>Provider {index + 1}</h4>{providers.length > 1 && <button className="text-button" onClick={() => setProviders((items) => items.filter((_, item) => item !== index))}>Remove</button>}</div>
              <div className="form-grid">{Object.entries(provider).map(([key, value]) => <label key={key}><span>{key.replace("_", " ")}</span><input type={key === "api_key" ? "password" : "text"} value={value} onChange={(event) => updateProvider(index, key as keyof ProviderInput, event.target.value)} /></label>)}</div>
              <button className="secondary" disabled={!provider.api_key || !provider.model} onClick={() => testConnection(index)}>Test connection</button>{connection[index] && <small className={connection[index].startsWith("CONNECTED") ? "connection-ok" : "connection-message"}>{connection[index]}</small>}
            </div>)}
            <button className="text-button add-provider" onClick={() => setProviders((items) => [...items, blankProvider(`provider-${items.length + 1}`)])}>+ Add provider</button>
            <h4>Test modules</h4>
            <div className="plugins">{plugins.map((plugin) => <div className="plugin-wrap" key={plugin.name}><label className="plugin"><input type="checkbox" checked={selected.includes(plugin.name)} onChange={() => setSelected((items) => items.includes(plugin.name) ? items.filter((item) => item !== plugin.name) : [...items, plugin.name])} /><span><strong>{plugin.name.replaceAll("_", " ")}</strong><small>{plugin.description}</small></span></label>{selected.includes(plugin.name) && <details className="settings"><summary>Parameters</summary><textarea value={settings[plugin.name] || "{}"} onChange={(event) => setSettings((current) => ({ ...current, [plugin.name]: event.target.value }))} spellCheck={false} /></details>}</div>)}</div>
            <details className="settings scoring-settings"><summary>Scoring and hard gates</summary><textarea value={scoring} onChange={(event) => setScoring(event.target.value)} spellCheck={false} /></details>
            {error && <div className="error">{error}</div>}
            <button disabled={busy || selected.length === 0} onClick={start}>{busy ? "Benchmark running…" : "Start benchmark"}</button>
          </section>
          <section className="panel monitor">
            <div className="panel-title"><span>02</span><div><h3>Live run</h3><p>{activeRun || "Waiting for a benchmark"}</p></div><Status value={latestStatus} /></div>
            <div className="progress"><i style={{ width: `${progress * 100}%` }} /></div><small className="progress-label">{Math.round(progress * 100)}% complete</small>
            <LiveChart events={events} />
            <div className="plugin-statuses">{Object.entries(pluginStates).map(([name, status]) => <div key={name}><span>{name}</span><Status value={status} /></div>)}</div>
            {latestMetrics && <details className="live-metrics"><summary>Current metrics</summary><pre>{JSON.stringify(latestMetrics, null, 2)}</pre></details>}
            <div className="event-list">{events.slice(-8).reverse().map((event, index) => <div className="event" key={`${event.type}-${index}`}><i /><span>{event.type.replace(".", " ")}</span><b>{event.plugin || event.provider || event.status}</b></div>)}{!events.length && <div className="empty">Runtime events and latency appear here.</div>}</div>
            {activeRun && !busy && <a className="report-link" href={`/api/runs/${activeRun}/report`} target="_blank">Open generated report ↗</a>}
          </section>
        </div>
        <section className="panel history">
          <div className="panel-title"><span>03</span><div><h3>Run history</h3><p>Providers, models, scores and qualification verdicts.</p></div></div>
          <table><thead><tr><th>Run</th><th>Providers</th><th>Score / verdict</th><th>Started</th><th>Status</th><th /></tr></thead><tbody>{runs.map((run) => <tr key={run.run_id}><td><code>{run.run_id}</code></td><td>{Object.entries(run.providers || {}).map(([name, provider]) => <div key={name}>{name} · {provider.model}</div>)}</td><td>{Object.entries(run.providers || {}).map(([name, provider]) => <div key={name}>{provider.score ?? "—"} {provider.verdict && <Status value={provider.verdict} />}</div>)}</td><td>{run.started_at ? new Date(run.started_at).toLocaleString() : "—"}</td><td><Status value={run.status} /></td><td><button className="text-button" onClick={() => loadResult(run.run_id)}>View</button></td></tr>)}{!runs.length && <tr><td colSpan={6} className="empty">No runs yet.</td></tr>}</tbody></table>
        </section>
        {result && <ResultPanel result={result} records={records} selectedProvider={rawProvider} onSelectProvider={(provider) => loadRecords(result.run_id, provider, Object.keys(result.providers).length)} onRerun={rerunPlugin} />}
      </main>
    </div>
  );
}
