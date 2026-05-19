import React from "react";
import StatusCard from "../../../../StatusCard";
import { IAgentScopeRuntimeMessage } from "../types";

/**
 * 重试状态消息卡片
 *
 * 当后端通过 metadata.retry_status 标记消息为重试状态时，
 * 使用 StatusCard 以 warning 样式渲染，与普通文本消息区分。
 */
const RetryStatusMessage = React.memo(function ({
  data,
}: {
  data: IAgentScopeRuntimeMessage;
}) {
  // 从 content 中提取文本作为标题
  const textParts: string[] = [];
  for (const item of data.content || []) {
    if (item.type === "text" && "text" in item && item.text) {
      textParts.push(item.text);
    }
  }
  const title = textParts.join(" ") || "正在重试...";

  return <StatusCard status="warning" title={title} />;
});

export default RetryStatusMessage;
