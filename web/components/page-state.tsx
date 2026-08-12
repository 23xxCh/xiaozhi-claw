export function ErrorMessage({ message }: { message: string | null }) {
  return message ? <div className="error">{message}</div> : null;
}

export function Loading() {
  return <div className="card muted">正在加载…</div>;
}
