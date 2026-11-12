import "./SegmentedControl.css";

/**
 * options: [{ value, label, icon?: Component, disabled?, title? }]
 */
export default function SegmentedControl({ options, value, onChange, ariaLabel, size = "md" }) {
  return (
    <div className={`segmented segmented--${size}`} role="radiogroup" aria-label={ariaLabel}>
      {options.map(({ value: v, label, icon: Icon, disabled, title }) => (
        <button
          key={v}
          type="button"
          role="radio"
          aria-checked={value === v}
          disabled={disabled}
          title={title}
          className={`segmented__option${value === v ? " is-active" : ""}`}
          onClick={() => onChange(v)}
        >
          {Icon && <Icon size={15} strokeWidth={2} aria-hidden="true" />}
          {label && <span>{label}</span>}
        </button>
      ))}
    </div>
  );
}
