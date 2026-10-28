import CouncilBoard from './CouncilBoard/CouncilBoard';
import FinalDecisionPanel from './FinalDecisionPanel/FinalDecisionPanel';
import Stage2Collapsible from './Stage2Collapsible/Stage2Collapsible';

/** The in-flight (or just failed) council turn, driven by the councilEvents.js run state. */
export default function LiveCouncilRun({ run }) {
  return (
    <div className="assistant-message new-format">
      <div className="message-label">LLM Council</div>
      {run.error && (
        <div className="run-error" role="alert">
          The run stopped: {run.error}
        </div>
      )}
      <div className="council-layout">
        <div className="council-left">
          <CouncilBoard run={run} />
          <Stage2Collapsible stage2={run.stage2} members={run.members} defaultExpanded={false} />
        </div>
        <div className="council-right">
          <FinalDecisionPanel decision={run.final} chairman={run.chairman} />
        </div>
      </div>
    </div>
  );
}
