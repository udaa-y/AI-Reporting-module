import { useMemo, useState } from "react";
import {
  AlertCircle, AlertTriangle, BarChart3, CalendarRange, CheckCircle2, Download, GitCompare,
  HelpCircle, Info, RefreshCw, Sparkles, Table2, ThumbsDown, ThumbsUp, Zap, Gauge,
} from "lucide-react";

import AnswerChart, { pickChart } from "./AnswerChart.jsx";
import Markdown from "./Markdown.jsx";

const REVIEW = {
  correct: { icon: CheckCircle2, text: "Verified by Vanna", cls: "ok" },
  incorrect: { icon: AlertTriangle, text: "May not answer the question exactly", cls: "warn" },
  uncertain: { icon: HelpCircle, text: "Could not be fully verified", cls: "muted" },
};

function downloadCsv(columns, rows, name) {
  const esc = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
  const csv = [columns.join(","), ...rows.map((r) => columns.map((c) => esc(r[c])).join(","))].join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: `${name}.csv` });
  a.click();
  URL.revokeObjectURL(url);
}

function ResultTable({ columns, rows }) {
  return (
    <div className="chat-table">
      <table>
        <thead><tr>{columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.slice(0, 100).map((r, i) => (
            <tr key={i}>{columns.map((c) => <td key={c}>{r[c] == null ? "—" : String(r[c])}</td>)}</tr>
          ))}
        </tbody>
      </table>
      {rows.length > 100 && <p className="chat-table__more">Showing 100 of {rows.length} rows — download CSV for all.</p>}
    </div>
  );
}

function SuccessAnswer({ message, onAsk, onFeedback, onExplain }) {
  const p = message.payload;
  const insights = p.insights;
  const fallback = useMemo(() => pickChart(p.columns, p.rows), [p.columns, p.rows]);
  // Chart order of preference: the AI's choice (after Explain) → the saved template's → automatic.
  const chosen = insights?.chart || p.chart;
  const spec = chosen ? (chosen.type === "table" ? null : chosen) : fallback;
  const [view, setView] = useState("chart");
  const showChart = view === "chart" && Boolean(spec);
  const review = REVIEW[p.sql_review?.verdict];
  const followups = insights?.followups?.length ? insights.followups : p.related || [];

  return (
    <>
      <div className="chat-badges">
        {p.source === "library" && (
          <span className="chat-badge is-verified" title="Answered from the verified query library — no AI call needed">
            <Zap size={12} aria-hidden="true" /> Verified answer
          </span>
        )}
        {p.period_label && (
          <span className="chat-badge"><CalendarRange size={12} aria-hidden="true" /> {p.period_label}</span>
        )}
        {p.compare && (
          <span className="chat-badge is-compare"><GitCompare size={12} aria-hidden="true" /> Branch comparison</span>
        )}
      </div>

      <Markdown text={message.content} />
      {message.insightsLoading && (
        <div className="chat-thinking-lines" aria-label="Analysing">
          <span /><span />
        </div>
      )}

      {p.row_count > 0 && (
        <div className="chat-result">
          <div className="chat-result__bar">
            {spec && (
              <div className="chat-toggle" role="tablist">
                <button type="button" className={showChart ? "is-active" : ""} onClick={() => setView("chart")}>
                  <BarChart3 size={13} /> Chart
                </button>
                <button type="button" className={!showChart ? "is-active" : ""} onClick={() => setView("table")}>
                  <Table2 size={13} /> Table
                </button>
              </div>
            )}
            <button type="button" className="chat-link" onClick={() => downloadCsv(p.columns, p.rows, "ask-ai-result")}>
              <Download size={13} /> CSV
            </button>
          </div>
          {showChart ? <AnswerChart spec={spec} rows={p.rows} /> : <ResultTable columns={p.columns} rows={p.rows} />}
        </div>
      )}

      {p.row_count > 0 && !insights && (
        <button
          type="button" className="chat-explain" onClick={() => onExplain(message)}
          disabled={message.insightsLoading}
        >
          <Sparkles size={14} aria-hidden="true" />
          {message.insightsLoading ? "Explaining…" : "Explain this"}
        </button>
      )}

      {review && (
        <p className={`chat-review is-${review.cls}`} title={p.sql_review.explanation || undefined}>
          <review.icon size={13} aria-hidden="true" /> {review.text}
        </p>
      )}

      {insights?.details && (
        <details className="chat-details" open>
          <summary>Full analysis</summary>
          <Markdown text={insights.details} />
        </details>
      )}

      <details className="chat-details">
        <summary>How I got this</summary>
        <pre className="chat-sql">{p.sql}</pre>
        {p.source === "library" && (
          <p className="chat-muted">Answered from the saved query “{p.template_id}”, no AI call needed.</p>
        )}
        {p.sql_review?.explanation && <p className="chat-muted">{p.sql_review.explanation}</p>}
        {p.sql_review?.issues?.length > 0 && (
          <ul className="chat-muted">{p.sql_review.issues.map((i) => <li key={i}>{i}</li>)}</ul>
        )}
        <p className="chat-muted">
          {p.row_count.toLocaleString()} rows · {Math.round(p.execution_time_ms || 0)} ms
          {p.sql_attempts > 1 ? ` · corrected after ${p.sql_attempts - 1} attempt(s)` : ""}
        </p>
        {p.source === "library" && (
          <button type="button" className="chat-link" onClick={() => onAsk(p.question, { forceLlm: true })}>
            <RefreshCw size={13} /> Not what you meant? Ask AI to write a fresh query
          </button>
        )}
      </details>

      {followups.length > 0 && (
        <div className="chat-followups">
          {followups.slice(0, 3).map((q) => (
            <button key={q} type="button" onClick={() => onAsk(q)}>{q}</button>
          ))}
        </div>
      )}

      {p.source !== "library" && (
        <div className="chat-feedback">
          <span>Was this right?</span>
          <button
            type="button" aria-label="Correct" onClick={() => onFeedback(message, true)}
            className={p.feedback?.correct === true ? "is-on" : ""}
            title="Saves this question so similar ones are answered instantly next time"
          ><ThumbsUp size={14} /></button>
          <button
            type="button" aria-label="Wrong" onClick={() => onFeedback(message, false)}
            className={p.feedback?.correct === false ? "is-on is-bad" : ""}
          ><ThumbsDown size={14} /></button>
        </div>
      )}
    </>
  );
}

export default function ChatMessage({ message, isLast, question, onAsk, onClarify, onFeedback, onExplain }) {
  if (message.role === "user") {
    return <div className={`chat-msg is-user${message.optimistic ? " is-sending" : ""}`}>{message.content}</div>;
  }
  const p = message.payload || {};
  return (
    <div className={`chat-msg is-assistant is-${p.status || "info"}`}>
      {p.status === "success" && (
        <SuccessAnswer message={message} onAsk={onAsk} onFeedback={onFeedback} onExplain={onExplain} />
      )}

      {p.status === "suggestions" && (
        <>
          <p className="chat-clarify-q"><Zap size={14} aria-hidden="true" /> {message.content}</p>
          <div className="chat-followups is-stacked">
            {p.suggestions.map((s) => (
              <button key={s.template_id} type="button" disabled={!isLast}
                      onClick={() => onAsk(s.question, { templateId: s.template_id })}>
                {s.question}
              </button>
            ))}
          </div>
          {isLast && question && (
            <button type="button" className="chat-link" onClick={() => onAsk(question, { forceLlm: true })}>
              <Sparkles size={13} /> None of these — answer my question with AI
            </button>
          )}
        </>
      )}

      {p.status === "info" && (
        <p className="chat-info"><Info size={15} aria-hidden="true" /> {message.content}</p>
      )}

      {p.status === "limit" && (
        <p className="chat-info is-limit"><Gauge size={15} aria-hidden="true" /> {message.content}</p>
      )}

      {p.status === "clarification_needed" && (
        <>
          <p className="chat-clarify-q">{message.content}</p>
          {p.options?.length > 0 && (
            <div className="chat-followups">
              {p.options.map((o) => (
                <button key={o} type="button" disabled={!isLast} onClick={() => onClarify(o)}>{o}</button>
              ))}
            </div>
          )}
          {isLast && <p className="chat-muted">Pick an option or type your answer below.</p>}
        </>
      )}

      {p.status === "error" && (
        <>
          <p className="chat-error"><AlertCircle size={15} aria-hidden="true" /> {message.content}</p>
          {(p.error_code || p.detail) && (
            <details className="chat-details">
              <summary>Technical details</summary>
              <p className="chat-muted">Error code: <code>{p.error_code || "unknown"}</code></p>
              {p.detail && <pre className="chat-sql">{p.detail}</pre>}
              {!p.detail && <p className="chat-muted">An administrator can see the full error message.</p>}
            </details>
          )}
        </>
      )}
    </div>
  );
}
