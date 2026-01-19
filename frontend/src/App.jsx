import { useState, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import ChatInterface from './components/ChatInterface';
import { api } from './api';
import './App.css';

function App() {
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  const [currentConversation, setCurrentConversation] = useState(null);
  const [isLoading, setIsLoading] = useState(false);

  // New state for real-time council tracking
  const [councilMembers, setCouncilMembers] = useState({});
  const [finalDecision, setFinalDecision] = useState({ status: 'empty', content: '' });
  const [stage2Data, setStage2Data] = useState({ rankings: null, labelToModel: null, aggregateRankings: null, isLoading: false });
  const [useNewUI, setUseNewUI] = useState(false);

  // Helper to update a specific council member
  const updateMember = (memberId, updates) => {
    setCouncilMembers((prev) => ({
      ...prev,
      [memberId]: {
        ...prev[memberId],
        ...(typeof updates === 'function' ? updates(prev[memberId]) : updates),
      },
    }));
  };

  // Reset council state for a new query
  const resetCouncilState = () => {
    setCouncilMembers({});
    setFinalDecision({ status: 'empty', content: '' });
    setStage2Data({ rankings: null, labelToModel: null, aggregateRankings: null, isLoading: false });
    setUseNewUI(true);
  };

  // Load conversations on mount
  useEffect(() => {
    loadConversations();
  }, []);

  // Load conversation details when selected
  useEffect(() => {
    if (currentConversationId) {
      loadConversation(currentConversationId);
    }
  }, [currentConversationId]);

  const loadConversations = async () => {
    try {
      const convs = await api.listConversations();
      setConversations(convs);
    } catch (error) {
      console.error('Failed to load conversations:', error);
    }
  };

  const loadConversation = async (id) => {
    try {
      const conv = await api.getConversation(id);
      setCurrentConversation(conv);
    } catch (error) {
      console.error('Failed to load conversation:', error);
    }
  };

  const handleNewConversation = async () => {
    try {
      const newConv = await api.createConversation();
      setConversations([
        { id: newConv.id, created_at: newConv.created_at, message_count: 0 },
        ...conversations,
      ]);
      setCurrentConversationId(newConv.id);
    } catch (error) {
      console.error('Failed to create conversation:', error);
    }
  };

  const handleSelectConversation = (id) => {
    setCurrentConversationId(id);
  };

  const handleSendMessage = async (content) => {
    if (!currentConversationId) return;

    setIsLoading(true);
    resetCouncilState();

    try {
      // Optimistically add user message to UI
      const userMessage = { role: 'user', content };
      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, userMessage],
      }));

      // Create a partial assistant message that will be updated progressively
      const assistantMessage = {
        role: 'assistant',
        stage1: null,
        stage2: null,
        stage3: null,
        metadata: null,
        loading: {
          stage1: false,
          stage2: false,
          stage3: false,
        },
        isNewFormat: true, // Mark as new format for rendering
      };

      // Add the partial assistant message
      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, assistantMessage],
      }));

      // Send message with streaming (use v2 endpoint for new UI)
      await api.sendMessageStreamV2(currentConversationId, content, (eventType, event) => {
        switch (eventType) {
          // New per-member events
          case 'member_queued':
            updateMember(event.memberId, {
              memberId: event.memberId,
              displayName: event.displayName,
              status: 'queued',
              progressText: '',
              latencyMs: null,
              error: null,
            });
            break;

          case 'member_streaming':
            updateMember(event.memberId, (prev) => ({
              ...prev,
              status: 'streaming',
              progressText: (prev?.progressText || '') + event.deltaText,
            }));
            break;

          case 'member_complete':
            updateMember(event.memberId, {
              status: 'done',
              progressText: event.response,
              latencyMs: event.latencyMs,
            });
            break;

          case 'member_error':
            updateMember(event.memberId, {
              status: 'error',
              error: event.message,
            });
            break;

          // Legacy stage1 complete (also update assistant message for storage)
          case 'stage1_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.stage1 = event.data;
              lastMsg.loading.stage1 = false;
              return { ...prev, messages };
            });
            break;

          // Stage 2 events
          case 'stage2_start':
            setStage2Data((prev) => ({ ...prev, isLoading: true }));
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.loading.stage2 = true;
              return { ...prev, messages };
            });
            break;

          case 'stage2_model_complete':
            // Individual ranking completed (for potential future per-model display)
            break;

          case 'stage2_complete':
            setStage2Data({
              rankings: event.data,
              labelToModel: event.metadata?.label_to_model,
              aggregateRankings: event.metadata?.aggregate_rankings,
              isLoading: false,
            });
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.stage2 = event.data;
              lastMsg.metadata = event.metadata;
              lastMsg.loading.stage2 = false;
              return { ...prev, messages };
            });
            break;

          // Final decision events
          case 'final_generating':
            setFinalDecision({ status: 'generating', content: '' });
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.loading.stage3 = true;
              return { ...prev, messages };
            });
            break;

          case 'final_streaming':
            setFinalDecision((prev) => ({
              status: 'streaming',
              content: prev.content + event.deltaText,
            }));
            break;

          case 'final_ready':
            setFinalDecision({
              status: 'ready',
              content: event.content,
              latencyMs: event.latencyMs,
            });
            break;

          // Legacy stage3 complete (for storage)
          case 'stage3_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastMsg = messages[messages.length - 1];
              lastMsg.stage3 = event.data;
              lastMsg.loading.stage3 = false;
              return { ...prev, messages };
            });
            break;

          case 'title_complete':
            loadConversations();
            break;

          case 'complete':
            loadConversations();
            setIsLoading(false);
            setUseNewUI(false); // Reset for next message
            break;

          case 'error':
            console.error('Stream error:', event.message);
            setFinalDecision((prev) => ({
              ...prev,
              status: 'error',
              error: event.message,
            }));
            setIsLoading(false);
            break;

          default:
            console.log('Unknown event type:', eventType);
        }
      });
    } catch (error) {
      console.error('Failed to send message:', error);
      setCurrentConversation((prev) => ({
        ...prev,
        messages: prev.messages.slice(0, -2),
      }));
      setIsLoading(false);
      setUseNewUI(false);
    }
  };

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
      />
      <ChatInterface
        conversation={currentConversation}
        onSendMessage={handleSendMessage}
        isLoading={isLoading}
        councilMembers={councilMembers}
        finalDecision={finalDecision}
        stage2Data={stage2Data}
        useNewUI={useNewUI}
      />
    </div>
  );
}

export default App;
