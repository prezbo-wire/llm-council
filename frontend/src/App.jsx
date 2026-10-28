import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Sidebar from './components/Sidebar';
import ChatInterface from './components/ChatInterface';
import CouncilPicker from './components/CouncilPicker/CouncilPicker';
import { api as defaultApi } from './api';
import { buildAssistantMessage, createRunState, reduceCouncilEvent } from './councilEvents.js';
import { CatalogContext } from './council/CatalogContext.js';
import {
  browserStorage,
  councilPayload,
  readLastCouncil,
  resolveInitialDraft,
  sameSelection,
  slugifyPresetName,
  validateDraft,
  writeLastCouncil,
} from './council/draft.js';
import { describeApiError } from './council/format.js';
import { appendTurn } from './council/turns.js';
import { useMeseResources } from './hooks/useMeseResources.js';
import './App.css';

/**
 * @param {{api?: ReturnType<typeof import('./api').createApi>, initialConversationId?: string|null}} props
 *   `api` is injectable so the dev replay (`?replay=<fixture>`) can run the real UI without a backend.
 */
function App({ api = defaultApi, initialConversationId = null }) {
  const mese = useMeseResources(api);
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(initialConversationId);
  const [currentConversation, setCurrentConversation] = useState(null);
  const [drafts, setDrafts] = useState({}); // conversation id → council draft edited in the picker
  const [lastUsed, setLastUsed] = useState(() => readLastCouncil(browserStorage()));
  const [pendingTurn, setPendingTurn] = useState(null); // {conversationId, content, run, active}
  const [notice, setNotice] = useState(null);
  const requestedIdRef = useRef(initialConversationId);

  const loadConversations = useCallback(
    () =>
      api.listConversations().then(
        (list) => setConversations(list),
        (error) => console.error('Failed to load conversations:', error),
      ),
    [api],
  );

  const loadConversation = useCallback(
    (id) =>
      api.getConversation(id).then(
        (conv) => {
          if (requestedIdRef.current !== id) return; // the user moved on to another conversation
          setCurrentConversation(conv);
          // A finished (failed) live turn is superseded by what the backend persisted.
          setPendingTurn((p) => (p && p.conversationId === id && !p.active ? null : p));
        },
        (error) => console.error('Failed to load conversation:', error),
      ),
    [api],
  );

  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  useEffect(() => {
    if (currentConversationId) loadConversation(currentConversationId);
  }, [currentConversationId, loadConversation]);

  /* ── Council draft (conversation.council → last used → builtin:hybrid) ── */
  const conversationId = currentConversation ? currentConversation.id : null;
  const presetItems = mese.presets.items;
  const draft = useMemo(() => {
    if (conversationId && drafts[conversationId]) return drafts[conversationId];
    return resolveInitialDraft({
      conversationCouncil: currentConversation ? currentConversation.council : null,
      lastUsed,
      presets: presetItems,
    }).draft;
  }, [conversationId, drafts, currentConversation, lastUsed, presetItems]);
  const validation = useMemo(() => validateDraft(draft, mese.index), [draft, mese.index]);
  const isLoading = Boolean(pendingTurn && pendingTurn.active);
  const catalogContext = useMemo(() => ({ index: mese.index, adminUrl: mese.adminUrl }), [mese.index, mese.adminUrl]);

  const handleDraftChange = (next) => {
    if (!conversationId) return;
    setDrafts((prev) => ({ ...prev, [conversationId]: next }));
  };

  /** Called by the picker only after the user changed the selection; skipped when it equals the stored one. */
  const handleCommitCouncil = async (committed) => {
    if (!conversationId || sameSelection(committed, currentConversation && currentConversation.council)) return;
    try {
      const res = await api.putConversationCouncil(conversationId, { ...councilPayload(committed), preset_id: committed.presetId });
      setCurrentConversation((prev) => (prev && prev.id === conversationId && res ? { ...prev, council: res.council } : prev));
    } catch (error) {
      setNotice({ text: `Couldn't save the council: ${describeApiError(error)}` });
    }
  };

  const handleSavePreset = async (name, current) => {
    const id = slugifyPresetName(name, presetItems.map((p) => p.id));
    try {
      await api.savePreset(id, { name, ...councilPayload(current) });
    } catch (error) {
      throw new Error(describeApiError(error));
    }
    await mese.refreshPresets();
    handleDraftChange({ ...current, presetId: id });
  };

  const handleDeletePreset = async (preset) => {
    try {
      await api.deletePreset(preset.id);
    } catch (error) {
      throw new Error(describeApiError(error));
    }
    await mese.refreshPresets();
  };

  /* ── Conversations ── */
  const handleNewConversation = async () => {
    try {
      const newConv = await api.createConversation();
      setConversations((prev) => [{ id: newConv.id, created_at: newConv.created_at, message_count: 0 }, ...prev]);
      requestedIdRef.current = newConv.id;
      setCurrentConversationId(newConv.id);
      setNotice(null);
    } catch (error) {
      setNotice({ text: describeApiError(error) });
    }
  };

  const handleSelectConversation = (id) => {
    requestedIdRef.current = id;
    setCurrentConversationId(id);
    setNotice(null);
  };

  /* ── Sending: stream §I.6 events through the reducer ── */
  const handleSendMessage = async (content) => {
    const targetId = conversationId;
    if (!targetId || isLoading) return false;
    if (!draft || !validation.valid) {
      setNotice({ text: validation.problems.join(' · ') || 'Pick a council first' });
      return false;
    }
    const council = councilPayload(draft);
    let run = createRunState();
    let completed = false;
    let failed = false;
    const patchTurn = (patch) => setPendingTurn((p) => (p && p.conversationId === targetId ? { ...p, ...patch } : p));

    setNotice(null);
    setPendingTurn({ conversationId: targetId, content, run, active: true });
    if (writeLastCouncil(browserStorage(), draft)) setLastUsed(draft);

    const onEvent = (event) => {
      run = reduceCouncilEvent(run, event, Date.now());
      patchTurn({ run });
      if (event.type === 'title_complete') loadConversations();
      if (event.type === 'complete') completed = true;
      if (event.type === 'error') failed = true;
    };

    try {
      await api.sendMessageStreamV2(targetId, content, council, onEvent);
    } catch (error) {
      if (run.phase === 'idle') {
        // Rejected before the run started (422 unknown_models, 503 mese_unreachable, …).
        setPendingTurn((p) => (p && p.conversationId === targetId ? null : p));
        setNotice({ text: describeApiError(error) });
        return false;
      }
      console.error('Stream failed:', error);
      run = reduceCouncilEvent(run, { type: 'error', message: 'Lost the connection to the backend' }, Date.now());
      failed = true;
    }

    if (!completed) {
      if (!failed) run = reduceCouncilEvent(run, { type: 'error', message: 'The run ended before it completed' }, Date.now());
      patchTurn({ run, active: false });
      loadConversations();
      return true;
    }

    // Show the finished turn immediately, then replace it with what the backend persisted.
    const assistant = buildAssistantMessage(run);
    setCurrentConversation((prev) =>
      prev && prev.id === targetId ? { ...prev, messages: appendTurn(prev.messages, content, assistant) } : prev,
    );
    setPendingTurn((p) => (p && p.conversationId === targetId ? null : p));
    loadConversations();
    if (requestedIdRef.current === targetId) loadConversation(targetId);
    return true;
  };

  const liveTurn = pendingTurn && pendingTurn.conversationId === conversationId ? pendingTurn : null;
  const picker = currentConversation ? (
    <CouncilPicker
      key={conversationId}
      catalog={mese.catalog}
      presets={mese.presets}
      draft={draft}
      validation={validation}
      disabled={isLoading}
      onDraftChange={handleDraftChange}
      onSavePreset={handleSavePreset}
      onDeletePreset={handleDeletePreset}
      onPlan={api.planCouncil}
      onCommit={handleCommitCouncil}
      onRetryCatalog={mese.refreshCatalog}
    />
  ) : null;

  return (
    <CatalogContext.Provider value={catalogContext}>
      <div className="app">
        <Sidebar
          conversations={conversations}
          currentConversationId={currentConversationId}
          onSelectConversation={handleSelectConversation}
          onNewConversation={handleNewConversation}
          meseHealth={mese.health}
          onRefreshHealth={mese.refreshHealth}
        />
        <ChatInterface
          conversation={currentConversation}
          liveTurn={liveTurn}
          onSendMessage={handleSendMessage}
          isLoading={isLoading}
          councilPicker={picker}
          canSend={validation.valid}
          notice={notice}
          onDismissNotice={() => setNotice(null)}
        />
      </div>
    </CatalogContext.Provider>
  );
}

export default App;
