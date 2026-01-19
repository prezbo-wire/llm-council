import CouncilMemberCard from './CouncilMemberCard';
import './CouncilBoard.css';

export default function CouncilBoard({ members }) {
  // Convert members object to array for rendering
  const memberArray = Object.values(members);

  if (memberArray.length === 0) {
    return null;
  }

  return (
    <div className="council-board">
      <h3 className="board-title">LLM Council</h3>
      <div className="members-grid">
        {memberArray.map((member) => (
          <CouncilMemberCard key={member.memberId} member={member} />
        ))}
      </div>
    </div>
  );
}
