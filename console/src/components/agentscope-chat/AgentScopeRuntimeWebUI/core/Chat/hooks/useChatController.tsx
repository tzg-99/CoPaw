import { sleep } from "@/components/agentscope-chat";
import { useCallback, useEffect, useRef } from "react";
import { useContextSelector } from "use-context-selector";
import { ChatAnywhereInputContext } from "../../Context/ChatAnywhereInputContext";
import { ChatAnywhereSessionsContext } from "../../Context/ChatAnywhereSessionsContext";
import {
  emit,
  default as useChatAnywhereEventEmitter,
} from "../../Context/useChatAnywhereEventEmitter";
import { IAgentScopeRuntimeWebUIMessage } from "@/components/agentscope-chat";
import { InputProps } from "../Input";
import useChatMessageHandler from "./useChatMessageHandler";
import useChatRequest from "./useChatRequest";
import useChatSessionHandler from "./useChatSessionHandler";
import useSuggestionsPolling from "./useSuggestionsPolling";
import usePostTurnValidationPolling from "./usePostTurnValidationPolling";
import { useChatAnywhereOptions } from "../../Context/ChatAnywhereOptionsContext";
import ReactDOM from "react-dom";
import { consumePostTurnValidation } from "@/api/modules/postTurnValidation";
import {
  FollowUpSubmitCoordinator,
  FOLLOW_UP_SUBMIT_FAILED_EVENT,
  RUNTIME_INPUT_SET_CONTENT_EVENT,
  type FollowUpSubmitData,
} from "./followUpSubmit";
import { shouldEnqueueFollowUpSubmission } from "./followUpSubmitState";
import type { CurrentQARef } from "./currentQARef";
import { createChatRequestOwner, type ChatRequestOwner } from "./requestOwnership";
// import mockdata from '../../mock/mock.json'

/**
 * 聊天控制器 Hook - 协调所有聊天相关操作
 */
export default function useChatController() {
  const setLoading = useContextSelector(
    ChatAnywhereInputContext,
    (v) => v.setLoading,
  );
  const getLoading = useContextSelector(
    ChatAnywhereInputContext,
    (v) => v.getLoading,
  );
  const currentSessionId = useContextSelector(
    ChatAnywhereSessionsContext,
    (v) => v.currentSessionId,
  );
  const sessionApi = useChatAnywhereOptions((v) => v.session.api);

  const currentQARef = useRef<CurrentQARef["current"]>({});
  const followUpCoordinatorRef = useRef<FollowUpSubmitCoordinator | null>(null);
  const followUpSessionIdRef = useRef<string | undefined>(undefined);

  // 消息处理
  const messageHandler = useChatMessageHandler({ currentQARef });

  // 会话处理
  const sessionHandler = useChatSessionHandler();

  // 建议轮询
  const { pollSuggestions } = useSuggestionsPolling({
    currentQARef,
    updateMessage: messageHandler.updateMessage,
  });
  const { fetchPendingValidation } = usePostTurnValidationPolling({
    currentQARef,
    updateMessage: messageHandler.updateMessage,
  });

  /**
   * 完成响应
   */
  const finishResponse = useCallback(
    (
      status: "finished" | "interrupted" = "finished",
      owner?: ChatRequestOwner,
    ) => {
      if (!currentQARef.current.response) return;

      currentQARef.current.response.msgStatus = status;
      setLoading(false);
      ReactDOM.flushSync(() => {
        messageHandler.updateMessage(currentQARef.current.response);
      });

      sessionHandler.syncSessionMessagesForSession(
        owner?.sessionId ?? currentQARef.current.activeRequestOwner?.sessionId,
        messageHandler.getMessages(),
        false,
      );

      if (status === "finished") {
        void (async () => {
          const hasPendingValidation = await fetchPendingValidation();
          if (!hasPendingValidation) {
            pollSuggestions();
          }
        })();
      }
    },
    [
      setLoading,
      messageHandler,
      sessionHandler,
      fetchPendingValidation,
      pollSuggestions,
    ],
  );

  // API 请求处理
  const { request, reconnect, cancelActiveRequest } = useChatRequest({
    currentQARef,
    updateMessage: messageHandler.updateMessage,
    getCurrentSessionId: sessionHandler.getCurrentSessionId,
    onFinish: (owner) => finishResponse("finished", owner),
  });

  const createRequestOwner = useCallback(
    (kind: ChatRequestOwner["kind"], sessionId: string): ChatRequestOwner => {
      const runtimeSessionApi = sessionApi as
        | {
            getLogicalSessionId?: (sessionId: string) => string;
            getChatIdForSession?: (sessionId: string) => string | null;
          }
        | undefined;

      return createChatRequestOwner({
        kind,
        sessionId,
        logicalSessionId:
          runtimeSessionApi?.getLogicalSessionId?.(sessionId) ?? sessionId,
        chatId: runtimeSessionApi?.getChatIdForSession?.(sessionId) ?? null,
      });
    },
    [sessionApi],
  );

  const submitTurn = useCallback(
    async (data: FollowUpSubmitData) => {
      await sessionHandler.ensureSession(data.query);
      const activeSessionId = sessionHandler.getCurrentSessionId();
      if (!activeSessionId) {
        return;
      }

      const messages = messageHandler.getMessages();
      if (activeSessionId) {
        await sessionHandler.updateSessionName(data.query, messages);
      }

      messageHandler.createRequestMessage(data);
      await sessionHandler.syncSessionMessagesForSession(
        activeSessionId,
        messageHandler.getMessages(),
        true,
      );
      setLoading(true);
      await sleep(100);

      messageHandler.createResponseMessage();
      const owner = createRequestOwner("submit", activeSessionId);
      currentQARef.current.activeRequestOwner = owner;

      const historyMessages = messageHandler.getHistoryMessages();
      await sessionHandler.syncSessionMessagesForSession(
        activeSessionId,
        messageHandler.getMessages(),
        true,
      );

      await request(historyMessages, data.biz_params, owner);
    },
    [createRequestOwner, messageHandler, request, sessionHandler, setLoading],
  );

  const isSessionGenerating = useCallback(async () => {
    const sessionId = sessionHandler.getCurrentSessionId();
    if (!sessionId || !sessionApi?.getSession) {
      return false;
    }

    try {
      const session = await sessionApi.getSession(sessionId);
      return Boolean(session?.generating);
    } catch {
      return false;
    }
  }, [sessionApi, sessionHandler]);

  const restorePendingInput = useCallback((data: FollowUpSubmitData) => {
    emit({
      type: RUNTIME_INPUT_SET_CONTENT_EVENT,
      data: {
        content: data.query,
        fileList: data.fileList,
        biz_params: data.biz_params,
      },
    });
  }, []);

  const notifyFollowUpFailure = useCallback(() => {
    emit({
      type: FOLLOW_UP_SUBMIT_FAILED_EVENT,
    });
  }, []);

  const stopActiveRunInBackground = useCallback(async () => {
    const owner = currentQARef.current.activeRequestOwner;
    await cancelActiveRequest();

    if (currentQARef.current.response) {
      currentQARef.current.response.msgStatus = "finished";
      ReactDOM.flushSync(() => {
        messageHandler.updateMessage(currentQARef.current.response!);
      });
    }

    await sessionHandler.syncSessionMessagesForSession(
      owner?.sessionId,
      messageHandler.getMessages(),
    );
  }, [cancelActiveRequest, messageHandler, sessionHandler]);

  if (!followUpCoordinatorRef.current) {
    followUpCoordinatorRef.current = new FollowUpSubmitCoordinator({
      stop: async () => {
        if (
          followUpSessionIdRef.current !== sessionHandler.getCurrentSessionId()
        ) {
          return;
        }

        await stopActiveRunInBackground();
      },
      submit: async (data) => {
        if (followUpSessionIdRef.current !== sessionHandler.getCurrentSessionId()) {
          return;
        }

        await submitTurn(data);
      },
      isGenerating: async () => {
        if (
          followUpSessionIdRef.current !== sessionHandler.getCurrentSessionId()
        ) {
          return false;
        }

        return isSessionGenerating();
      },
      restoreInput: (query) => {
        if (
          followUpSessionIdRef.current !== sessionHandler.getCurrentSessionId()
        ) {
          return;
        }

        restorePendingInput(query);
      },
      notifyFailure: () => {
        if (
          followUpSessionIdRef.current !== sessionHandler.getCurrentSessionId()
        ) {
          return;
        }

        notifyFollowUpFailure();
      },
    });
  }

  /**
   * 处理用户提交
   */
  const handleSubmit = useCallback<InputProps["onSubmit"]>(
    async (data) => {
      const generating = shouldEnqueueFollowUpSubmission(
        Boolean(getLoading?.()),
        await isSessionGenerating(),
      );

      if (generating) {
        followUpSessionIdRef.current = sessionHandler.getCurrentSessionId();
        await followUpCoordinatorRef.current?.enqueue(data);
        return;
      }

      await submitTurn(data);
    },
    [getLoading, isSessionGenerating, submitTurn],
  );

  const handleApproval = useCallback(
    async ({ input }) => {
      messageHandler.createApprovalMessage(input);
      const activeSessionId = sessionHandler.getCurrentSessionId();
      if (!activeSessionId) {
        return;
      }

      setLoading(true);
      await sessionHandler.syncSessionMessagesForSession(
        activeSessionId,
        messageHandler.getMessages(),
        true,
      );
      await sleep(100);

      messageHandler.createResponseMessage();
      const owner = createRequestOwner("approval", activeSessionId);
      currentQARef.current.activeRequestOwner = owner;
      const historyMessages = messageHandler.getHistoryMessages();
      await sessionHandler.syncSessionMessagesForSession(
        activeSessionId,
        messageHandler.getMessages(),
        true,
      );

      await request(historyMessages, undefined, owner);
    },
    [createRequestOwner, messageHandler, request, sessionHandler, setLoading],
  );

  /**
   * 处理取消
   */
  const handleCancel = useCallback(() => {
    cancelActiveRequest();
    finishResponse("interrupted", currentQARef.current.activeRequestOwner);
  }, [cancelActiveRequest, finishResponse]);

  const updatePostTurnValidationStatus = useCallback(
    (
      validationId: string,
      status: "dismissed" | "consumed",
    ): boolean => {
      const latestResponse = currentQARef.current.response;
      const card = latestResponse?.cards?.[0];
      const validation = card?.data?.post_turn_validation;
      if (!latestResponse || !card?.data || validation?.id !== validationId) {
        return false;
      }

      const updatedCards = [
        {
          ...card,
          data: {
            ...card.data,
            post_turn_validation: {
              ...validation,
              status,
            },
          },
        },
        ...latestResponse.cards.slice(1),
      ];

      currentQARef.current.response = {
        ...latestResponse,
        cards: updatedCards,
      };
      ReactDOM.flushSync(() => {
        messageHandler.updateMessage(currentQARef.current.response!);
      });
      return true;
    },
    [messageHandler],
  );

  const handlePostTurnValidationContinue = useCallback(
    async (validationId: string) => {
      if (!validationId || getLoading?.()) {
        return;
      }

      const activeSessionId = sessionHandler.getCurrentSessionId();
      if (!activeSessionId) {
        return;
      }

      const consumed = await consumePostTurnValidation({
        validationId,
        sessionId: activeSessionId,
      });
      if (!consumed) {
        return;
      }

      updatePostTurnValidationStatus(validationId, "consumed");
      await submitTurn({
        query: "继续执行上一步任务",
        fileList: [],
        biz_params: {
          post_turn_validation_resume_id: validationId,
        },
      });
    },
    [
      getLoading,
      sessionHandler,
      submitTurn,
      updatePostTurnValidationStatus,
    ],
  );

  const handlePostTurnValidationDismiss = useCallback(
    async (validationId: string) => {
      if (!validationId) {
        return;
      }

      const activeSessionId = sessionHandler.getCurrentSessionId();
      if (activeSessionId) {
        await consumePostTurnValidation({
          validationId,
          sessionId: activeSessionId,
        });
      }
      updatePostTurnValidationStatus(validationId, "dismissed");
      pollSuggestions();
    },
    [pollSuggestions, sessionHandler, updatePostTurnValidationStatus],
  );

  const handleSuggestionSubmit = useCallback(
    async (data: FollowUpSubmitData) => {
      if (!data?.query || getLoading?.()) {
        return;
      }

      await submitTurn({
        query: data.query,
        fileList: data.fileList || [],
        biz_params: data.biz_params,
      });
    },
    [getLoading, submitTurn],
  );

  /**
   * 处理重新生成
   */
  const handleRegenerate = useCallback(
    async (messageId: string) => {
      const activeSessionId = sessionHandler.getCurrentSessionId();
      if (!activeSessionId) {
        return;
      }

      setLoading(true);

      // 1. 移除旧消息
      messageHandler.removeMessageById(messageId);

      // 2. 创建新的响应消息
      currentQARef.current.abortController = new AbortController();
      messageHandler.createResponseMessage();
      const owner = createRequestOwner("regenerate", activeSessionId);
      currentQARef.current.activeRequestOwner = owner;

      // 3. 发起请求
      const historyMessages = messageHandler.getHistoryMessages();
      await request(historyMessages, undefined, owner);
    },
    [createRequestOwner, messageHandler, request, sessionHandler, setLoading],
  );

  /**
   * 处理 SSE 重连（切回未完成的对话时）
   */
  const handleReconnect = useCallback(
    async (sessionId: string) => {
      currentQARef.current.abortController = new AbortController();
      setLoading(true);

      messageHandler.createResponseMessage();
      const owner = createRequestOwner("reconnect", sessionId);
      currentQARef.current.activeRequestOwner = owner;

      await reconnect(sessionId, owner);
    },
    [createRequestOwner, messageHandler, reconnect, setLoading],
  );

  // 监听会话切换，断开当前 SSE 连接（不通知后端取消）并重置状态
  useEffect(() => {
    followUpSessionIdRef.current = undefined;
    currentQARef.current.abortController?.abort();
    currentQARef.current = {
      request: undefined,
      response: undefined,
      abortController: undefined,
      activeRequestOwner: undefined,
    };
  }, [currentSessionId]);

  // 监听重连事件
  useChatAnywhereEventEmitter(
    {
      type: "handleReconnect",
      callback: async (data) => {
        await handleReconnect(data.detail.session_id);
      },
    },
    [handleReconnect],
  );

  // 监听重新生成事件
  useChatAnywhereEventEmitter({
    type: "handleReplace",
    callback: async (data) => {
      await handleRegenerate(data.detail.id);
    },
  });

  useChatAnywhereEventEmitter(
    {
      type: "handleSubmit",
      callback: async (data) => {
        await handleSubmit(data.detail);
      },
    },
    [handleSubmit],
  );

  useChatAnywhereEventEmitter(
    {
      type: "handleSuggestionSubmit",
      callback: async (data) => {
        await handleSuggestionSubmit(data.detail);
      },
    },
    [handleSuggestionSubmit],
  );

  useChatAnywhereEventEmitter(
    {
      type: "handleApproval",
      callback: async (data) => {
        await handleApproval(data.detail);
      },
    },
    [handleApproval],
  );

  useChatAnywhereEventEmitter(
    {
      type: "handlePostTurnValidationContinue",
      callback: async (data) => {
        await handlePostTurnValidationContinue(data.detail?.validation_id);
      },
    },
    [handlePostTurnValidationContinue],
  );

  useChatAnywhereEventEmitter(
    {
      type: "handlePostTurnValidationDismiss",
      callback: async (data) => {
        await handlePostTurnValidationDismiss(data.detail?.validation_id);
      },
    },
    [handlePostTurnValidationDismiss],
  );

  return {
    handleSubmit,
    handleCancel,
  };
}
