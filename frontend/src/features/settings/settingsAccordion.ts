export type SettingsAccordionState = Record<string, boolean>;

export function createCollapsedSectionState(sectionIds: string[]): SettingsAccordionState {
  return sectionIds.reduce<SettingsAccordionState>((state, sectionId) => ({
    ...state,
    [sectionId]: false,
  }), {});
}

export function toggleSection(state: SettingsAccordionState, sectionId: string): SettingsAccordionState {
  return {
    ...state,
    [sectionId]: !state[sectionId],
  };
}

export function setAllSectionsOpen(sectionIds: string[], open: boolean): SettingsAccordionState {
  return sectionIds.reduce<SettingsAccordionState>((state, sectionId) => ({
    ...state,
    [sectionId]: open,
  }), {});
}
