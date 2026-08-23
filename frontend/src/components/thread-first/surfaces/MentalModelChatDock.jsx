import React, { useCallback, useEffect, useRef, useState } from "react";

/**
 * Compact chat dock for the mental model graph overlay.
 */
export default function MentalModelChatDock({
  messages = [],
  onSend,
  onOpenInMainThread,
  disabled = false,
  placeholder = "Ask about this design or say “run Cadence”…",
}) {
  const [text, setText] = useState("");
  const scrollRef = useRef(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  const submit = useCallback(() => {
    const value = text.trim();
    if (!value || disabled) return;
    onSend?.(value);
    setText("");
  }, [text, disabled, onSend]);

  return (
    <div className="tf-mm-chat-dock">
      {messages.length ? (
        <div className="tf-mm-chat-log" ref={scrollRef}>
          {messages.slice(-6).map((m) => (
            <div key={m.id} className={`tf-mm-chat-line ${m.role}`}>
              <span className="role">{m.role === "user" ? "You" : "Agent"}</span>
              <span className="body">{m.text}</span>
            </div>
          ))}
        </div>
      ) : null}
      <div className="tf-mm-chat-input-row">
        <input
          className="tf-mm-chat-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <button type="button" className="tf-btn sm primary" onClick={submit} disabled={disabled || !text.trim()}>
          Send
        </button>
        {onOpenInMainThread ? (
          <button type="button" className="tf-btn sm ghost" onClick={onOpenInMainThread} title="Continue in main thread">
            Main
          </button>
        ) : null}
      </div>
    </div>
  );
}
