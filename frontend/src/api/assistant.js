import { apiGet, apiPost, apiRequest } from "./client.js";

// See app/api/routes/assistant.py
export const fetchScopes = () => apiGet("/assistant/scopes");
export const fetchConversations = () => apiGet("/assistant/conversations");
export const fetchConversation = (id) => apiGet(`/assistant/conversations/${id}`);
export const deleteConversation = (id) => apiRequest(`/assistant/conversations/${id}`, { method: "DELETE" });

export const askQuestion = ({ scope, question, conversationId, branchId, compareBranchIds, templateId, forceLlm }) =>
  apiPost("/assistant/ask", {
    scope,
    question,
    conversation_id: conversationId ?? null,
    branch_id: branchId ?? null,
    compare_branch_ids: compareBranchIds ?? [],   // Super Admin "Compare branches"
    template_id: templateId ?? null,              // a "Did you mean…?" choice
    force_llm: Boolean(forceLlm),                 // "Ask AI to write a fresh query"
  });
  
export const answerClarification = ({ conversationId, answer }) =>
  apiPost("/assistant/clarify", { conversation_id: conversationId, answer });

export const fetchInsights = (messageId) => apiPost(`/assistant/messages/${messageId}/insights`, {});

export const sendFeedback = (messageId, correct) =>
  apiPost(`/assistant/messages/${messageId}/feedback`, { correct });
