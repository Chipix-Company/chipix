import { createUserChatMessagePayload } from "./chatSendPayload.js";

export async function dispatchUserChatMessage({
  sendMessage,
  append,
  text,
  requestOptions,
}) {
  if (typeof sendMessage === "function") {
    await sendMessage(createUserChatMessagePayload(text), requestOptions);
    return "sendMessage";
  }
  if (typeof append === "function") {
    await append({ role: "user", content: String(text ?? "") }, requestOptions);
    return "append";
  }
  throw new Error("No supported chat send function available");
}
