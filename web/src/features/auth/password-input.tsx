"use client";

import { useState, type ComponentProps } from "react";

function EyeIcon({ off }: { off: boolean }) {
  return (
    <svg aria-hidden="true" className="size-5" fill="none" viewBox="0 0 20 20">
      <path
        d="M2.5 10S5.2 5.5 10 5.5 17.5 10 17.5 10 14.8 14.5 10 14.5 2.5 10 2.5 10Z"
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="1.6"
      />
      <circle cx="10" cy="10" r="2.2" stroke="currentColor" strokeWidth="1.6" />
      {off ? (
        <path
          d="M4 16 16 4"
          stroke="currentColor"
          strokeLinecap="round"
          strokeWidth="1.6"
        />
      ) : null}
    </svg>
  );
}

export function PasswordInput({
  className = "",
  disabled,
  ...props
}: Omit<ComponentProps<"input">, "type">) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="password-field">
      <input
        {...props}
        className={`input ${className}`.trim()}
        disabled={disabled}
        type={visible ? "text" : "password"}
      />
      <button
        aria-label={visible ? "Hide password" : "Show password"}
        aria-pressed={visible}
        className="password-toggle"
        disabled={disabled}
        type="button"
        onClick={() => setVisible((value) => !value)}
      >
        <EyeIcon off={visible} />
      </button>
    </div>
  );
}
