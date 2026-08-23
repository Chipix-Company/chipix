import React, { useEffect, useRef, useState } from "react";
import { X, Terminal, AlertCircle, PlayCircle } from "lucide-react";

function NewProjectModal({ isOpen, onClose, onCreate, isDarkTheme = true }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const modalRef = useRef(null);
  const inputRef = useRef(null);

  // Reset transient state whenever the modal is opened/closed. The component
  // stays mounted between opens, so loading must not leak into the next create.
  useEffect(() => {
    if (isOpen) {
      setName("");
      setDescription("");
      setError("");
      setIsLoading(false);
      // Focus input after render
      setTimeout(() => inputRef.current?.focus(), 0);
    } else {
      setError("");
      setIsLoading(false);
    }
  }, [isOpen]);

  // Keyboard accessibility - Escape to close
  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  // Handle click outside to close
  const handleOverlayClick = (event) => {
    if (event.target === event.currentTarget) {
      onClose();
    }
  };

  if (!isOpen) {
    return null;
  }

  const handleSubmit = async (event) => {
    event.preventDefault();
    const trimmedName = name.trim();

    if (!trimmedName) {
      setError("Project name is required");
      return;
    }

    setIsLoading(true);
    setError("");

    try {
      const created = await onCreate(trimmedName, description.trim());
      if (created === false) {
        setError("Failed to create project. Please try again.");
        setIsLoading(false);
        return;
      }

      setName("");
      setDescription("");
      setIsLoading(false);
      onClose();
    } catch (err) {
      setError(err.message || "An error occurred while creating the project");
      setIsLoading(false);
    }
  };

  return (
    <div
      className="modal-overlay"
      data-ui-shell="thread-first"
      data-tf-theme={isDarkTheme ? "dark" : "light"}
      role="dialog"
      aria-modal="true"
      onClick={handleOverlayClick}
    >
      <form className="modal-card" onSubmit={handleSubmit} ref={modalRef} key={isOpen ? 'open' : 'closed'}>
        <div className="modal-header">
           <h3 className="modal-title">Initialize_Project</h3>
            <button type="button" onClick={onClose} className="modal-close-btn">
                <X size={18} strokeWidth={2.5} />
            </button>
        </div>

        <div className="modal-content">
          <p className="modal-subtitle">Configure workspace parameters for the new verification suite.</p>

          <div className="modal-form-field">
            <label className="modal-label" htmlFor="project-name-input">
              System_Identifier
            </label>
            <div className="relative">
              <input
                ref={inputRef}
                id="project-name-input"
                value={name}
                onChange={(event) => {
                  setName(event.target.value);
                  setError("");
                }}
                className="modal-input"
                placeholder="e.g. SPI_CONTROLLER_MASTER"
                disabled={isLoading}
              />
            </div>
          </div>

          <div className="modal-form-field">
            <label className="modal-label" htmlFor="project-description-input">
              Metadata_Summary
            </label>
            <textarea
              id="project-description-input"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              className="modal-input modal-textarea"
              placeholder="Provide context for the verification team..."
              disabled={isLoading}
            />
          </div>

          {error ? (
            <div className="modal-error">
              <AlertCircle size={14} />
              {error}
            </div>
          ) : null}

          <div className="modal-actions">
            <button
              type="button"
              className="modal-btn secondary"
              onClick={onClose}
              disabled={isLoading}
            >
              <X size={14} />
              Abort_Op
            </button>
            <button
              type="submit"
              className="modal-btn primary modal-btn-wide"
              disabled={isLoading || !name.trim()}
            >
              {isLoading ? (
                <>
                  <div className="animate-spin rounded-full h-3 w-3 border-b-2 border-white"></div>
                  Provisioning...
                </>
              ) : (
                <>
                  <PlayCircle size={14} />
                  Provision_Project
                </>
              )}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

export default NewProjectModal;



