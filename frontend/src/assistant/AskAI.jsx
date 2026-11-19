import { useEffect, useRef, useState } from "react";
import {
  ArrowUp, Building2, CalendarCheck, Check, ChevronLeft, ClipboardList, GitCompare, History,
  MessageSquare, MessageSquarePlus, Pill, Sparkles, Stethoscope, Trash2, Users, X, Gauge, Activity,
} from "lucide-react";

import { useAuth } from "../auth/AuthContext.jsx";
import ChatMessage from "./ChatMessage.jsx";
import { useChat } from "./useChat.js";
import { useBranch } from "../branch/BranchContext.jsx";
import "./AskAI.css";

const MAX_CHARS = 1000;

function timeAgo(iso) {
  const mins = Math.round((Date.now() - new Date(iso)) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 24 * 60) return `${Math.round(mins / 60)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

// Icon names used by sections in app/assistant/scopes.py — add new ones here.
const SCOPE_ICONS = { CalendarCheck, Stethoscope, Pill, Users, Building2, ClipboardList, MessageSquare, Activity };

function ScopeIcon({ name, size = 14 }) {
  const Icon = SCOPE_ICONS[name] || MessageSquare;
  return <Icon size={size} aria-hidden="true" />;
}

/** "AI questions left" bar. Saved (verified) questions are free and not counted. */
function UsageBar({ usage }) {
  if (!usage || !usage.limit) return null;              // limit 0 = unlimited: no bar
  const left = usage.remaining;
  const level = left === 0 ? " is-empty" : left <= Math.max(3, usage.limit * 0.2) ? " is-low" : "";
  const resets = usage.resets_at
    ? new Date(usage.resets_at).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" })
    : null;
  return (
    <div
      className={`askai__usage${level}`}
      title={`AI-written answers in the last ${usage.window_hours} hours. Verified (saved) answers don't count.`}
    >
      <div className="askai__usage-text">
        <span><Gauge size={12} aria-hidden="true" /> AI questions</span>
        <span>
          <strong>{left}</strong> of {usage.limit} left
          {left === 0 && resets ? ` · next at ${resets}` : ""}
        </span>
      </div>
      <div className="askai__usage-track" role="progressbar" aria-label="AI questions used"
           aria-valuemin={0} aria-valuemax={usage.limit} aria-valuenow={usage.used}>
        <span style={{ width: `${(usage.used / usage.limit) * 100}%` }} />
      </div>
    </div>
  );
}

/**
 * Super Admin: which branches Ask AI answers for.
 *   value [] (all ticked) → combined data · one id → that branch only · 2+ ids → side-by-side comparison
 */
function BranchPicker({ branches, value, onChange }) {
  const [open, setOpen] = useState(false);
  const all = value.length === 0;
  const ticked = all ? branches.map((b) => b.id) : value;
  const set = (ids) => onChange(ids.length >= branches.length ? [] : ids);   // every branch ticked = all
  const toggle = (id) => {
    const next = ticked.includes(id) ? ticked.filter((v) => v !== id) : [...ticked, id];
    if (next.length) set(next);                                              // keep at least one ticked
  };
  const one = value.length === 1 ? branches.find((b) => b.id === value[0]) : null;
  const label = all ? "All branches" : one ? one.name : `Comparing ${value.length} branches`;
  const mode = all ? ""
    : one ? ``
      : "";
  return (
    <span className="askai__compare">
      <button
        type="button" className={`askai__context is-compare${all ? "" : " is-on"}`}
        onClick={() => setOpen((o) => !o)} aria-expanded={open} title={mode}
      >
        {value.length >= 2 ? <GitCompare size={12} aria-hidden="true" /> : <Building2 size={12} aria-hidden="true" />}
        {label}
      </button>
      {!all && (
        <button type="button" className="askai__compare-clear" onClick={() => onChange([])} aria-label="Back to all branches">
          <X size={12} />
        </button>
      )}
      {open && (
        <div className="askai__compare-panel" role="listbox" aria-multiselectable="true" aria-label="Branches">
          
          <button type="button" role="option" aria-selected={all}
                  className={`askai__compare-option${all ? " is-selected" : ""}`} onClick={() => onChange([])}>
            <span className="askai__compare-check">{all && <Check size={11} strokeWidth={3} />}</span>
            All branches
          </button>
          <div className="askai__compare-divider" />
          {branches.map((b) => (
            <div key={b.id} className="askai__compare-row">
              <button type="button" role="option" aria-selected={ticked.includes(b.id)}
                      className={`askai__compare-option${ticked.includes(b.id) ? " is-selected" : ""}`}
                      onClick={() => toggle(b.id)}>
                <span className="askai__compare-check">{ticked.includes(b.id) && <Check size={11} strokeWidth={3} />}</span>
                {b.name}
              </button>
              <button type="button" className="askai__compare-only" onClick={() => set([b.id])}
                      title={`Only ${b.name}`}>only</button>
            </div>
          ))}
          <p className="askai__compare-mode">{mode}</p>
          <button type="button" className="askai__compare-done" onClick={() => setOpen(false)}>Done</button>
        </div>
      )}
    </span>
  );
}

/** Floating "Ask AI" button + slide-in chat panel. Mounted once in AppLayout. */
export default function AskAI() {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [view, setView] = useState("chat");             // "chat" | "history"
  const [text, setText] = useState("");
  const { branchId, current: currentBranch, branches, canSwitch } = useBranch();

  // Super Admin's Ask AI branches: [] = all, [id] = that branch only, 2+ = compare.
  // Starts from the branch picked in the top bar and follows it when that changes.
  const [pickedIds, setPickedIds] = useState([]);
  useEffect(() => setPickedIds(branchId === null ? [] : [branchId]), [branchId]);
  const picked = canSwitch ? pickedIds.filter((id) => branches.some((b) => b.id === id)) : [];
  const comparing = picked.length >= 2;
  const askBranchId = !canSwitch ? branchId : picked.length === 1 ? picked[0] : null;
  const chat = useChat(open && Boolean(user), askBranchId, comparing ? picked : []);
  const listRef = useRef(null);
  const inputRef = useRef(null);

  // Ctrl/⌘ + K toggles, Esc closes.
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (open && view === "chat") setTimeout(() => inputRef.current?.focus(), 200);
  }, [open, view]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [chat.messages, chat.pending]);

  if (!user) return null;

  const current = chat.scopes.find((s) => s.key === chat.scope);
  const last = chat.messages[chat.messages.length - 1];
  const awaitingClarification = last?.payload?.status === "clarification_needed";

  const submit = (value = text, options = {}) => {
    const q = value.trim();
    if (!q || chat.pending || !chat.scope) return;
    setText("");
    if (awaitingClarification && !options.templateId && !options.forceLlm) chat.clarify(q);
    else chat.ask(q, options);
  };

  const showHistory = () => {
    setView("history");
    chat.loadHistory();
  };

  return (
    <>
      <button
        type="button"
        className={`askai-fab${open ? " is-hidden" : ""}`}
        onClick={() => setOpen(true)}
        aria-label="Ask AI (Ctrl+K)"
      >
        <Sparkles size={18} aria-hidden="true" />
        <span>Ask AI</span>
      </button>

      <aside className={`askai${open ? " is-open" : ""}`} aria-hidden={!open} aria-label="Ask AI">
        <header className="askai__header">
          {view === "history" ? (
            <button type="button" className="askai__icon-btn" onClick={() => setView("chat")} aria-label="Back to chat">
              <ChevronLeft size={18} />
            </button>
          ) : (
            <span className="askai__logo"><Sparkles size={16} /></span>
          )}
          <div className="askai__title">
            <strong>{view === "history" ? "Recent chats" : "Ask AI"}</strong>
            {view === "chat" && <span>{chat.conversation?.title || "Ask questions about your clinic data"}</span>}
          </div>
          {view === "chat" && (
            <>
              <button type="button" className="askai__icon-btn" onClick={showHistory} title="Recent chats">
                <History size={17} />
              </button>
              <button type="button" className="askai__icon-btn" onClick={chat.newChat} title="New chat">
                <MessageSquarePlus size={17} />
              </button>
            </>
          )}
          <button type="button" className="askai__icon-btn" onClick={() => setOpen(false)} title="Close (Esc)">
            <X size={18} />
          </button>
        </header>

        {view === "history" ? (
          <div className="askai__history">
            {chat.history === null && <p className="chat-muted askai__pad">Loading…</p>}
            {chat.history?.length === 0 && <p className="chat-muted askai__pad">No chats yet.</p>}
            {chat.history?.map((c) => {
              const s = chat.scopes.find((x) => x.key === c.scope);
              return (
                <div key={c.id} className={`askai__conv${chat.conversation?.id === c.id ? " is-current" : ""}`}>
                  <button type="button" className="askai__conv-open" onClick={() => { chat.openConversation(c.id); setView("chat"); }}>
                    <span className="askai__conv-title">{c.title}</span>
                    <span className="askai__conv-meta">
                      {s && <ScopeIcon name={s.icon} size={12} />} {s?.label ?? c.scope} · {timeAgo(c.updated_at)}
                    </span>
                  </button>
                  <button type="button" className="askai__icon-btn" title="Delete chat" onClick={() => chat.removeConversation(c.id)}>
                    <Trash2 size={15} />
                  </button>
                </div>
              );
            })}
          </div>
        ) : (
          <>
            <div className="askai__scopes" role="radiogroup" aria-label="Section">
              {chat.scopes.map((s) => (
                <button
                  key={s.key}
                  type="button"
                  role="radio"
                  aria-checked={chat.scope === s.key}
                  title={s.description}
                  className={`askai__scope${chat.scope === s.key ? " is-active" : ""}`}
                  onClick={() => chat.setScope(s.key)}
                >
                  <ScopeIcon name={s.icon} /> {s.label}
                </button>
              ))}
            </div>

            <div className="askai__messages" ref={listRef}>
              {chat.messages.length === 0 && current && (
                <div className="askai__welcome">
                  <span className="askai__welcome-icon"><ScopeIcon name={current.icon} size={20} /></span>
                  <h3>Ask about {current.label}</h3>
                  <p>{current.description}</p>
                  <div className="chat-followups is-stacked">
                    {current.starters.map((q) => (
                      <button key={q} type="button" onClick={() => submit(q)}>{q}</button>
                    ))}
                  </div>
                    {current.compare_starters?.length > 0 && (
                    <>
                      <p className="askai__welcome-sub"><GitCompare size={13} aria-hidden="true" /> Compare branches</p>
                      <div className="chat-followups is-stacked">
                        {current.compare_starters.map((q) => (
                          <button key={q} type="button" onClick={() => submit(q)}>{q}</button>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              )}

              {chat.messages.map((m, i) => (
                  <ChatMessage
                  key={m.id}
                  message={m}
                  isLast={i === chat.messages.length - 1}
                  question={chat.messages[i - 1]?.role === "user" ? chat.messages[i - 1].content : undefined}
                  onAsk={(q, options) => submit(q, options)}
                  onClarify={(a) => submit(a)}
                  onFeedback={chat.feedback}
                  onExplain={chat.explain}
                />
              ))}

              {chat.pending && (
                <div className="chat-msg is-assistant is-pending" aria-live="polite">
                  <span className="chat-dots"><i /><i /><i /></span> Finding the answer in {current?.label ?? "your data"}…
                </div>
              )}
              {chat.error && <p className="chat-error askai__pad">{chat.error}</p>}
            </div>

            <form className="askai__composer" onSubmit={(e) => { e.preventDefault(); submit(); }}>
              <UsageBar usage={chat.usage} />
              {current && !awaitingClarification && (
                <div className="askai__contexts">
                  <span className="askai__context"><ScopeIcon name={current.icon} size={12} /> {current.label}</span>
                  {canSwitch && branches.length > 1 ? (
                    <BranchPicker branches={branches} value={picked} onChange={setPickedIds} />
                  ) : (
                    <span className="askai__context is-branch">
                      <Building2 size={12} aria-hidden="true" /> {branchId === null ? "All branches" : currentBranch?.name ?? "Your branch"}
                    </span>
                  )}
                </div>
              )}
              <div className="askai__input">
                <textarea
                  ref={inputRef}
                  rows={1}
                  value={text}
                  maxLength={MAX_CHARS}
                  placeholder={awaitingClarification ? "Type your answer…" : "Ask a question…"}
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      submit();
                    }
                  }}
                />
                <button type="submit" disabled={!text.trim() || chat.pending} aria-label="Send">
                  <ArrowUp size={18} />
                </button>
              </div>
              <p className="askai__hint">Enter to send · Shift+Enter for a new line · Ctrl+K to open/close</p>
            </form>
          </>
        )}
      </aside>
    </>
  );
}
