import { forwardRef, type InputHTMLAttributes } from "react";

// Adapted from shadcn/ui Input; keeps native attributes and visible focus/invalid states.
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className = "", ...props }, ref) {
    return (
      <input
        ref={ref}
        data-slot="input"
        className={`input ${className}`.trim()}
        {...props}
      />
    );
  },
);
