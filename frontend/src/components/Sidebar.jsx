import MesePill from './MesePill';
import './Sidebar.css';

export default function Sidebar({
  conversations,
  currentConversationId,
  onSelectConversation,
  onNewConversation,
  meseHealth,
  onRefreshHealth,
}) {
  const visibleConversations = conversations.filter(
    (conv) => conv.message_count > 0
  );

  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <h1>LLM Council</h1>
        <button className="new-conversation-btn" type="button" onClick={onNewConversation}>
          + New Conversation
        </button>
      </div>

      <div className="conversation-list">
        {visibleConversations.length === 0 ? (
          <div className="no-conversations">No conversations yet</div>
        ) : (
          visibleConversations.map((conv) => (
            <div
              key={conv.id}
              className={`conversation-item ${
                conv.id === currentConversationId ? 'active' : ''
              }`}
              onClick={() => onSelectConversation(conv.id)}
            >
              <div className="conversation-title">
                {conv.title || 'New Conversation'}
              </div>
              <div className="conversation-meta">
                {conv.message_count} messages
              </div>
            </div>
          ))
        )}
      </div>

      <div className="sidebar-footer">
        <MesePill health={meseHealth} onRefresh={onRefreshHealth} />
      </div>
    </div>
  );
}
