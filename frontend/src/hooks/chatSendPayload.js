export function createUserChatMessagePayload(text) {
  const cleanText = String(text ?? "").trim();
  return {
    role: "user",
    parts: [{ type: "text", text: cleanText }],
  };
}
