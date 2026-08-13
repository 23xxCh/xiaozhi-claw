export function BrandFace({ small = false }: { small?: boolean }) {
  return (
    <span className={`face${small ? " small" : ""}`} aria-hidden="true">
      <span className="face-eye left" />
      <span className="face-eye right" />
      <span className="face-mouth" />
    </span>
  );
}
