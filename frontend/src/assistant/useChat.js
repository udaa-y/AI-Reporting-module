import { useCallback, useEffect, useRef, useState } from "react";

import * as api from "../api/assistant.js";

const SCOPE_KEY = "askai.scope";

/** All Ask AI state: sections, current conversation, history and actions. */
export function useChat(enabled, branchId = null, compareBranchIds = []) {
  const [scopes, setScopes] = useState([]);
  const [scope, setScopeState] = useState(null);
  const [conversation, setConversation] = useState(null);   // { id, title, scope }
  const [messages, setMessages] = useState([]);
  const [history, setHistory] = useState(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const conversationRef = useRef(null);
  conversationRef.current = conversation;
  const branchRef = useRef(branchId);
  branchRef.current = branchId;
  const compareRef = useRef(compareBranchIds);
  compareRef.current = compareBranchIds;

  // Load the sections this user may ask about (once the panel is first opened).
  useEffect(() => {
    if (!enabled || scopes.length) return;
    api.fetchScopes().then(({ scopes: list }) => {
      setScopes(list);
      const saved = localStorage.getItem(SCOPE_KEY);
      setScopeState(list.some((s) => s.key === saved) ? saved : list[0]?.key ?? null);
    }).catch((e) => setError(e.message));
  }, [enabled, scopes.length]);

  const setScope = useCallback((key) => {
    setScopeState(key);
    try { localStorage.setItem(SCOPE_KEY, key); } catch { /* storage unavailable */ }
  }, []);

  const patchMessage = useCallback((id, patch) => {
    setMessages((ms) => ms.map((m) => (m.id === id ? { ...m, ...patch(m) } : m)));
  }, []);

  const loadInsights = useCallback(async (message) => {
    patchMessage(message.id, () => ({ insightsLoading: true }));
    try {
      const { content, insights } = await api.fetchInsights(message.id);
      if (insights?.error) console.warn("[Ask AI] Analysis fell back to computed figures:", insights.error);
      patchMessage(message.id, (m) => ({
        content, insightsLoading: false, payload: { ...m.payload, insights },
      }));
    } catch (e) {
      console.error("[Ask AI] Analysis request failed:", e.message);
      patchMessage(message.id, () => ({ insightsLoading: false }));
    }
  }, [patchMessage]);

  const receive = useCallback((result) => {
    setConversation(result.conversation);
    setMessages((ms) => [...ms.filter((m) => !m.optimistic), ...result.messages]);
    const reply = result.messages[result.messages.length - 1];
    if (reply?.payload?.status === "error") {
      // Shows the exact provider/database error (detail is included for admins).
      console.error(`[Ask AI] ${reply.payload.error_code || "error"}:`, reply.payload.detail || reply.payload.message);
    }
        // The AI analysis is NOT loaded automatically: the user clicks "Explain this".
  }, []);

  const run = useCallback(async (text, call) => {
    setPending(true);
    setError(null);
    setMessages((ms) => [...ms, { id: `tmp-${Date.now()}`, role: "user", content: text, optimistic: true }]);
    try {
      receive(await call());
    } catch (e) {
      console.error("[Ask AI] Request failed:", e.message);
      setMessages((ms) => ms.filter((m) => !m.optimistic));
      setError(e.message);
    } finally {
      setPending(false);
    }
  }, [receive]);

    /** options: { templateId } to run a suggested library question, { forceLlm } to skip the library. */
  const ask = useCallback((question, options = {}) =>
    run(question, () => api.askQuestion({
      scope, question, conversationId: conversationRef.current?.id, branchId: branchRef.current,
      compareBranchIds: compareRef.current, ...options,
    })), [run, scope]);

  const clarify = useCallback((answer) =>
    run(answer, () => api.answerClarification({
      conversationId: conversationRef.current.id, answer,
    })), [run]);

  const newChat = useCallback(() => {
    setConversation(null);
    setMessages([]);
    setError(null);
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      setHistory((await api.fetchConversations()).conversations);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  const openConversation = useCallback(async (id) => {
    setError(null);
    try {
      const conv = await api.fetchConversation(id);
      const { messages: list, ...rest } = conv;
      setConversation(rest);
      setMessages(list);
      setScope(rest.scope);
    } catch (e) {
      setError(e.message);
    }
  }, [setScope]);

  const removeConversation = useCallback(async (id) => {
    await api.deleteConversation(id);
    setHistory((h) => h?.filter((c) => c.id !== id) ?? h);
    if (conversationRef.current?.id === id) newChat();
  }, [newChat]);

  const feedback = useCallback(async (message, correct) => {
    patchMessage(message.id, (m) => ({ payload: { ...m.payload, feedback: { correct } } }));
    try {
      await api.sendFeedback(message.id, correct);
    } catch (e) {
      setError(e.message);
    }
  }, [patchMessage]);

  return {
    scopes, scope, setScope, conversation, messages, history, pending, error,
    ask, clarify, newChat, loadHistory, openConversation, removeConversation,
    feedback, explain: loadInsights,
  };
}
