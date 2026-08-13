import { ArrowClockwiseIcon } from "@phosphor-icons/react/dist/ssr/ArrowClockwise";
import { WarningCircleIcon } from "@phosphor-icons/react/dist/ssr/WarningCircle";

export function ErrorMessage({ message }: { message: string | null }) {
  return message ? (
    <div className="error section-state" role="alert" aria-live="assertive">
      <WarningCircleIcon size={20} weight="fill" />
      <span>{message}</span>
    </div>
  ) : null;
}

export function SectionError({
  message,
  onRetry,
  updatedAt,
}: {
  message: string;
  onRetry?: () => void;
  updatedAt?: Date | null;
}) {
  return (
    <div className="card stack" role="status" aria-live="polite">
      <div className="error section-state">
        <WarningCircleIcon size={20} weight="fill" />
        <div>
          <strong>这一部分暂时没有更新</strong>
          <div>{message}</div>
        </div>
      </div>
      {updatedAt ? <div className="hint">仍显示上次数据 · {updatedAt.toLocaleTimeString()}</div> : null}
      {onRetry ? (
        <button className="button secondary" type="button" onClick={onRetry}>
          <ArrowClockwiseIcon size={18} /> 重新加载
        </button>
      ) : null}
    </div>
  );
}

export function Loading({ cards = 3 }: { cards?: number }) {
  return (
    <div className="grid" aria-label="正在加载" aria-busy="true">
      {Array.from({ length: cards }, (_, index) => (
        <div className="skeleton" key={index} />
      ))}
    </div>
  );
}

export function InlineResult({
  tone = "success",
  children,
}: {
  tone?: "success" | "warning";
  children: React.ReactNode;
}) {
  return (
    <div className={tone} role="status" aria-live="polite">
      {children}
    </div>
  );
}
