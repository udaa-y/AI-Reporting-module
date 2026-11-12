import { useEffect, useRef, useState } from "react";
import {
  ArrowUp, Building2, CalendarCheck, Check, ChevronLeft, ClipboardList, GitCompare, History,
  MessageSquare, MessageSquarePlus, Pill, Sparkles, Stethoscope, Trash2, Users, X,
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
const SCOPE_ICONS = { CalendarCheck, Stethoscope, Pill, Users, Building2, ClipboardList, MessageSquare };

function ScopeIcon({ name, size = 14 }) {
  const Icon = SCOPE_ICONS[name] || MessageSquare;
  return <Icon size={size} aria-hidden="true" />;
}

/** Super Admin: pick 2+ branches to compare side by side in Ask AI answers. */
function ComparePicker({ branches, value, onChange }) {
  const [open, setOpen] = useState(false);
  const toggle = (id) => onChange(value.includes(id) ? value.filter((v) => v !== id) : [...value, id]);
  const active = value.length >= 2;
  return (
    <span className="askai__compare">
      <button
        type="button" className={`askai__context is-compare${active ? " is-on" : ""}`}
        onClick={() => setOpen((o) => !o)} aria-expanded={open}
      >
        <GitCompare size={12} aria-hidden="true" />
        {active ? `Comparing ${value.length} branches` : "Compare branches"}
      </button>
      {active && (
        <button type="button" className="askai__compare-clear" onClick={() => onChange([])} aria-label="Stop comparing">
          <X size={12} />
        </button>
      )}
      {open && (
        <div className="askai__compare-panel" role="listbox" aria-multiselectable="true">
          <p className="chat-muted">Pick two or more branches</p>
          {branches.map((b) => (
            <button key={b.id} type="button" role="option" aria-selected={value.includes(b.id)}
                    className={`askai__compare-option${value.includes(b.id) ? " is-selected" : ""}`}
                    onClick={() => toggle(b.id)}>
              <span className="askai__compare-check">{value.includes(b.id) && <Check size={11} strokeWidth={3} />}</span>
              {b.name}
            </button>
          ))}
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
  const [compareIds, setCompareIds] = useState([]);
  const comparing = canSwitch && compareIds.length >= 2;
  const chat = useChat(open && Boolean(user), branchId, comparing ? compareIds : []);
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
              {current && !awaitingClarification && (
                <div className="askai__contexts">
                  <span className="askai__context"><ScopeIcon name={current.icon} size={12} /> {current.label}</span>
                  {!comparing && (
                    <span className="askai__context is-branch">
                      <Building2 size={12} aria-hidden="true" /> {branchId === null ? "All branches" : currentBranch?.name ?? "Your branch"}
                    </span>
                  )}
                  {canSwitch && branches.length > 1 && (
                    <ComparePicker branches={branches} value={compareIds} onChange={setCompareIds} />
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
