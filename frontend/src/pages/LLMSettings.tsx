import { useEffect, useState } from "react";
import { api, token, type Me } from "../api";

type Provider = "none" | "openai" | "anthropic";
type Profile = {
  model: string;
  reasoning_effort: string;
  thinking_mode: string;
  thinking_budget: number;
  max_output_tokens: number;
  timeout_seconds: number;
  requests_per_minute: number;
};
type PublicProfile = Profile & {has_api_key: boolean; key_source: string};
type Settings = PublicProfile & {provider: Provider; storage: string; profiles: Record<Provider, PublicProfile>};
type TestResult = {ok: boolean; message: string; latency_ms?: number; tokens_in?: number; tokens_out?: number};
const names = {none: "Offline templates", openai: "OpenAI", anthropic: "Anthropic"};
const endpoints = {none: "No external requests", openai: "https://api.openai.com/v1/responses", anthropic: "https://api.anthropic.com/v1/messages"};
const defaults: Profile = {model: "", reasoning_effort: "default", thinking_mode: "default", thinking_budget: 1024, max_output_tokens: 4096, timeout_seconds: 60, requests_per_minute: 30};

export function LLMSettingsPage() {
  const [saved, setSaved] = useState<Settings | null>(null);
  const [provider, setProvider] = useState<Provider>("none");
  const [profile, setProfile] = useState<Profile>(defaults);
  const [apiKey, setApiKey] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [operatorToken, setOperatorToken] = useState(token.get());
  const [canEdit, setCanEdit] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [test, setTest] = useState<TestResult | null>(null);
  const fields = (p: Profile): Profile => ({model: p.model, reasoning_effort: p.reasoning_effort, thinking_mode: p.thinking_mode, thinking_budget: p.thinking_budget, max_output_tokens: p.max_output_tokens, timeout_seconds: p.timeout_seconds, requests_per_minute: p.requests_per_minute});

  async function load() {
    setBusy(true); setError("");
    try {
      const [settings, me] = await Promise.all([api<Settings>("/intelligence/settings"), api<Me>("/am/v1/me")]);
      setSaved(settings); setProvider(settings.provider); setProfile(fields(settings));
      setCanEdit(me.permissions.includes("llm:configure")); setApiKey(""); setClearKey(false);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  useEffect(() => { void load(); }, []);
  const change = <K extends keyof Profile>(key: K, value: Profile[K]) => {
    setProfile(p => ({...p, [key]: value})); setTest(null); setNotice("");
  };
  function selectProvider(next: Provider) {
    setProvider(next); setProfile(fields(saved?.profiles[next] ?? defaults));
    setApiKey(""); setClearKey(false); setTest(null); setNotice(""); setError("");
  }
  async function submit(testOnly: boolean) {
    setBusy(true); setError(""); setNotice(""); setTest(null);
    const json = {...profile, provider, api_key: apiKey || null, clear_api_key: clearKey};
    try {
      if (testOnly) {
        setTest(await api<TestResult>("/intelligence/settings/test", {method: "POST", json}));
      } else {
        const result = await api<Settings>("/intelligence/settings", {method: "PUT", json});
        setSaved(result); setApiKey(""); setClearKey(false);
        setNotice("Saved and applied. New requests use these settings; completed advice stays in the history.");
      }
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }
  const keyStatus = saved?.profiles[provider];
  return <div className="page" style={{maxWidth: 1100}}>
    <h1>LLM settings</h1>
    <p className="muted">Choose the model used by the advisor, ask box and field-report preview.</p>
    <div className="card" style={{padding: 20, marginBottom: 20}}>
      <label>Operator token <input type="password" autoComplete="off" value={operatorToken} onChange={e => setOperatorToken(e.target.value)} placeholder="Only needed when server authentication is enabled" style={{width: "min(420px, 100%)", margin: "0 12px"}} /></label>
      <button className="btn" disabled={busy} onClick={() => {token.set(operatorToken); void load();}}>Use token / reload</button>
      {saved && <p className="muted">Active: <strong>{names[saved.provider]}</strong>{saved.provider !== "none" && ` · ${saved.model} · ${saved.has_api_key ? "key configured" : "no key — offline fallback"}`}</p>}
    </div>
    {error && <p role="alert" style={{color: "#fca5a5"}}>{error}</p>}
    {notice && <p role="status" style={{color: "#86efac"}}>{notice}</p>}
    {saved && !canEdit && <p>Only an administrator can change settings or test a provider.</p>}
    {saved && <form onSubmit={e => {e.preventDefault(); void submit(false);}}>
      <fieldset disabled={busy || !canEdit} style={{border: 0, padding: 0, margin: 0}}>
        <section className="card" style={{padding: 24, marginBottom: 20}}>
          <h2>Provider and access</h2>
          <div style={{display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 20}}>
            <label>Provider<select value={provider} onChange={e => selectProvider(e.target.value as Provider)} style={{display: "block", width: "100%", marginTop: 8}}>
              <option value="none">Offline templates (no API)</option><option value="openai">OpenAI</option><option value="anthropic">Anthropic</option>
            </select></label>
            {provider !== "none" && <label>Model ID<input required value={profile.model} maxLength={150} onChange={e => change("model", e.target.value)} style={{display: "block", width: "100%", marginTop: 8}} placeholder="Enter the exact API model ID" /></label>}
          </div>
          <p className="muted">API endpoint: {endpoints[provider]}</p>
          {provider !== "none" && <>
            <label>API key<input type="password" autoComplete="new-password" spellCheck={false} value={apiKey} disabled={clearKey} maxLength={4096} onChange={e => {setApiKey(e.target.value); setTest(null);}} placeholder={keyStatus?.has_api_key ? "Key configured — leave blank to keep it" : "Paste your provider API key"} style={{display: "block", width: "100%", marginTop: 8}} /></label>
            <p className="muted">{keyStatus?.has_api_key ? `A key is available from ${keyStatus.key_source === "environment" ? "the server environment" : "local settings"}.` : "No key is configured for this provider."} Saved keys are never returned to the browser or written to the audit log.</p>
            <label><input type="checkbox" checked={clearKey} onChange={e => {setClearKey(e.target.checked); setApiKey(""); setTest(null);}} /> Remove this provider’s key when saving (also disables its environment-key fallback)</label>
          </>}
        </section>
        {provider !== "none" && <section className="card" style={{padding: 24, marginBottom: 20}}>
          <h2>Thinking and limits</h2>
          <p className="muted">Supported thinking options depend on the model. “Provider default” sends no override. More thinking can take longer and use more tokens.</p>
          <div style={{display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 20}}>
            {provider === "anthropic" && <label>Thinking mode<select value={profile.thinking_mode} onChange={e => change("thinking_mode", e.target.value)} style={{display: "block", width: "100%", marginTop: 8}}>
              <option value="default">Provider default</option><option value="disabled">Disabled (if supported)</option><option value="adaptive">Adaptive thinking</option><option value="enabled">Manual token budget (older models)</option>
            </select></label>}
            <label>{provider === "openai" ? "Reasoning effort" : "Thinking effort"}<select value={profile.reasoning_effort} onChange={e => change("reasoning_effort", e.target.value)} style={{display: "block", width: "100%", marginTop: 8}}>
              {(provider === "openai" ? ["default", "none", "minimal", "low", "medium", "high", "xhigh", "max"] : ["default", "low", "medium", "high", "max"]).map(v => <option key={v} value={v}>{v === "default" ? "Provider default" : v}</option>)}
            </select></label>
            {provider === "anthropic" && profile.thinking_mode === "enabled" && <label>Thinking token budget<input type="number" required min={1024} max={Math.min(64000, profile.max_output_tokens - 1)} value={profile.thinking_budget} onChange={e => change("thinking_budget", Number(e.target.value))} style={{display: "block", width: "100%", marginTop: 8}} /></label>}
            <label>Maximum output tokens<input type="number" required min={256} max={128000} value={profile.max_output_tokens} onChange={e => change("max_output_tokens", Number(e.target.value))} style={{display: "block", width: "100%", marginTop: 8}} /></label>
            <label>Request timeout (seconds)<input type="number" required min={5} max={180} value={profile.timeout_seconds} onChange={e => change("timeout_seconds", Number(e.target.value))} style={{display: "block", width: "100%", marginTop: 8}} /></label>
            <label>Requests per minute<input type="number" required min={1} max={120} value={profile.requests_per_minute} onChange={e => change("requests_per_minute", Number(e.target.value))} style={{display: "block", width: "100%", marginTop: 8}} /></label>
          </div>
          <p className="muted">Output limits include thinking tokens. For a manual budget, leave additional output tokens for the final answer.</p>
        </section>}
        <div style={{display: "flex", gap: 12, alignItems: "center"}}>
          <button className="btn" type="submit">{busy ? "Working…" : "Save and apply"}</button>
          <button className="btn" type="button" disabled={provider === "none"} onClick={() => void submit(true)}>Test connection</button>
        </div>
      </fieldset>
    </form>}
    <p className="muted">Test sends only a short “OK” prompt to the selected provider and does not save changes. Saving an enabled provider allows subsequent intelligence requests to send their evidence context to that provider.</p>
    <p className="muted">{saved?.storage === "memory" ? "This server uses temporary in-memory settings." : "Settings and keys persist on this machine in a git-ignored file readable only by the server’s OS user. The local file is not encrypted."}</p>
    {test && <section className="card" role="status" style={{padding: 20, borderColor: test.ok ? "#258658" : "#a45d38"}}>
      <h3>{test.ok ? "Connection succeeded" : "Connection not verified"}</h3><p>{test.message}</p>
      {test.latency_ms != null && <p className="muted">{test.latency_ms} ms · {test.tokens_in ?? "—"} input / {test.tokens_out ?? "—"} output tokens</p>}
    </section>}
  </div>;
}
