import { useState, useEffect, useRef } from 'react';
import Markdown from './Markdown';
import AssistantMessage from './AssistantMessage';
import LiveCouncilRun from './LiveCouncilRun';
import { endsWithUserMessage } from '../council/turns.js';
import './ChatInterface.css';

function UserMessage({ content }) {
  return (
    <div className="user-message">
      <div className="message-label">You</div>
      <div className="message-content">
        <div className="markdown-content">
          <Markdown>{content}</Markdown>
        </div>
      </div>
    </div>
  );
}

/**
 * Conversation view: persisted turns, the live council run, and the input with the council
 * picker above it. Send is disabled while a run is in flight or the council is invalid.
 */
export default function ChatInterface({
  conversation,
  liveTurn = null,
  onSendMessage,
  isLoading,
  councilPicker = null,
  canSend = true,
  notice = null,
  onDismissNotice,
}) {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [conversation, liveTurn]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    const text = input;
    if (!text.trim() || isLoading || !canSend) return;
    setInput('');
    const accepted = await onSendMessage(text);
    // Restore the question when the backend rejected it before the run started.
    if (accepted === false) setInput((current) => current || text);
  };

  const handleKeyDown = (e) => {
    // Submit on Enter (without Shift)
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  if (!conversation) {
    return (
      <div className="chat-interface">
        <div className="empty-state">
          <h2>Welcome to LLM Council</h2>
          <p>Create a new conversation to get started</p>
        </div>
      </div>
    );
  }

  const messages = Array.isArray(conversation.messages) ? conversation.messages : [];
  const showPendingUser = liveTurn && !endsWithUserMessage(messages, liveTurn.content);
  const isEmpty = messages.length === 0 && !liveTurn;

  return (
    <div className="chat-interface">
      <div className="messages-container">
        {isEmpty ? (
          <div className="empty-state">
            <h2>Start a conversation</h2>
            <p>Ask a question to consult the LLM Council</p>
          </div>
        ) : (
          messages.map((msg, index) => (
            <div key={index} className="message-group">
              {msg.role === 'user' ? <UserMessage content={msg.content} /> : <AssistantMessage message={msg} />}
            </div>
          ))
        )}

        {liveTurn && (
          <div className="message-group">
            {showPendingUser && <UserMessage content={liveTurn.content} />}
            <LiveCouncilRun run={liveTurn.run} />
          </div>
        )}

        {isLoading && liveTurn && liveTurn.run.phase === 'idle' && (
          <div className="loading-indicator">
            <div className="spinner"></div>
            <span>Consulting the council...</span>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {notice && (
        <div className="chat-notice" role="alert">
          <span>{notice.text}</span>
          {onDismissNotice && (
            <button type="button" className="chat-notice-dismiss" aria-label="Dismiss" onClick={onDismissNotice}>
              ×
            </button>
          )}
        </div>
      )}

      {councilPicker}

      <form className="input-form" onSubmit={handleSubmit}>
        <textarea
          className="message-input"
          placeholder="Ask your question... (Shift+Enter for new line, Enter to send)"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isLoading}
          rows={3}
        />
        <button
          type="submit"
          className="send-button"
          disabled={!input.trim() || isLoading || !canSend}
          title={canSend ? undefined : 'Pick 1–8 members and a chairman'}
        >
          Send
        </button>
      </form>
    </div>
  );
}
