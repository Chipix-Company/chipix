/**
 * Joyride options aligned with thread-first CSS tokens (light / dark).
 */

export function buildJoyrideOptions(isDarkTheme = true) {
  return {
    primaryColor: isDarkTheme ? "#6eb5ff" : "#2563eb",
    backgroundColor: isDarkTheme ? "#221f1a" : "#ffffff",
    textColor: isDarkTheme ? "#f0ebe0" : "#1a1814",
    arrowColor: isDarkTheme ? "#221f1a" : "#ffffff",
    overlayColor: isDarkTheme ? "rgba(0, 0, 0, 0.78)" : "rgba(26, 24, 20, 0.62)",
    width: 380,
    zIndex: 12000,
    skipBeacon: true,
    spotlightPadding: 10,
    spotlightRadius: 10,
    showProgress: true,
    buttons: ["back", "skip", "primary"],
    closeButtonAction: "skip",
    overlayClickAction: false,
    locale: {
      back: "Back",
      close: "Close",
      last: "Done",
      next: "Next",
      nextWithProgress: "Next ({current} of {total})",
      skip: "Skip tour",
    },
  };
}

export function buildJoyrideStyles(isDarkTheme = true) {
  const inkMuted = isDarkTheme ? "#8f897b" : "#4a463e";
  return {
    tooltipTitle: {
      fontFamily: "var(--font-sans, system-ui, sans-serif)",
      fontSize: 15,
      fontWeight: 600,
      marginBottom: 6,
    },
    tooltipContent: {
      fontFamily: "var(--font-sans, system-ui, sans-serif)",
      fontSize: 13,
      lineHeight: 1.55,
      color: inkMuted,
      padding: "4px 0 8px",
    },
    buttonPrimary: {
      borderRadius: 6,
      fontSize: 11,
      fontWeight: 600,
      padding: "5px 10px",
      lineHeight: 1.25,
      minHeight: "unset",
    },
    buttonBack: {
      borderRadius: 6,
      fontSize: 11,
      padding: "4px 8px",
      color: inkMuted,
    },
    buttonSkip: {
      fontSize: 11,
      padding: "4px 8px",
      color: inkMuted,
    },
  };
}
