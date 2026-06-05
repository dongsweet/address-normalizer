import type { ConfigStatus, NormalizeBatchResponse, NormalizeStreamEvent, NormalizedAddress } from "./types";

export async function getConfigStatus(): Promise<ConfigStatus> {
  const response = await fetch("/api/config/status");
  if (!response.ok) {
    throw new Error(`配置状态获取失败: ${response.status}`);
  }
  return response.json();
}

export async function normalizeBatch(
  addresses: string[],
  useQwen: boolean,
  autoPersistMemory: boolean,
  concurrency = 2
): Promise<NormalizeBatchResponse> {
  const response = await fetch("/api/normalize/batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      addresses,
      use_qwen: useQwen,
      auto_persist_memory: autoPersistMemory,
      persist_job: true,
      concurrency: concurrency
    })
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `地址规范化失败: ${response.status}`);
  }
  return response.json();
}

export async function normalizeBatchStream(
  addresses: string[],
  useQwen: boolean,
  autoPersistMemory: boolean,
  concurrency: number,
  onEvent: (event: NormalizeStreamEvent) => void
): Promise<void> {
  const response = await fetch("/api/normalize/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      addresses,
      use_qwen: useQwen,
      auto_persist_memory: autoPersistMemory,
      persist_job: true,
      concurrency: concurrency
    })
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `地址规范化失败: ${response.status}`);
  }
  if (!response.body) {
    throw new Error("浏览器不支持流式响应");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      const text = line.trim();
      if (text) onEvent(JSON.parse(text) as NormalizeStreamEvent);
    }
  }
  buffer += decoder.decode();
  const text = buffer.trim();
  if (text) onEvent(JSON.parse(text) as NormalizeStreamEvent);
}

export async function confirmResult(result: NormalizedAddress): Promise<void> {
  const response = await fetch("/api/feedback/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      raw_address: result.input,
      normalized_address: result.normalized_address,
      components: result.components,
      anchor_type: result.anchor_type || "business_memory",
      anchor_id: result.anchor_id,
      anchor_source: result.source,
      lon: typeof result.components.lon === "number" ? result.components.lon : null,
      lat: typeof result.components.lat === "number" ? result.components.lat : null,
      confirmed_by: "demo"
    })
  });
  if (!response.ok) {
    throw new Error(`沉淀失败: ${response.status}`);
  }
}
