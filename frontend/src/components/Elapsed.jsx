import { useEffect, useState } from 'react';
import { formatElapsed } from '../council/format.js';

/** Live "12 s" counter since `since` (epoch ms). Renders nothing without a start time. */
export default function Elapsed({ since }) {
  const [now, setNow] = useState(null);

  useEffect(() => {
    if (typeof since !== 'number') return undefined;
    const tick = () => setNow(Date.now());
    const first = setTimeout(tick, 0);
    const timer = setInterval(tick, 1000);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [since]);

  if (typeof since !== 'number' || now === null) return null;
  return <span className="elapsed">{formatElapsed(Math.max(0, now - since))}</span>;
}
