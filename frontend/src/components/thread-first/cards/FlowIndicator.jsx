/** Minimal phase rail marker for staged verification cards. */
export default function FlowIndicator({ phase }) {
  if (!phase) return null;
  return <span className="tf-flow-ind" data-phase={phase} aria-hidden />;
}
