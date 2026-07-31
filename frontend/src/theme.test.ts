import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyTheme, getEffectiveTheme, getStoredTheme, setTheme, toggleTheme } from "./theme";

function mockSystemPrefersDark(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({ matches, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
  );
}

describe("theme", () => {
  beforeEach(() => {
    document.documentElement.removeAttribute("data-theme");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("getStoredTheme is null when nothing has been chosen yet", () => {
    expect(getStoredTheme()).toBeNull();
  });

  it("setTheme persists the choice and applies it to the document root", () => {
    setTheme("dark");
    expect(localStorage.getItem("theme")).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("setTheme(null) clears the override, falling back to the OS preference", () => {
    setTheme("dark");
    setTheme(null);
    expect(localStorage.getItem("theme")).toBeNull();
    expect(document.documentElement.getAttribute("data-theme")).toBeNull();
  });

  it("getEffectiveTheme follows the OS preference when no override is stored", () => {
    mockSystemPrefersDark(true);
    expect(getEffectiveTheme()).toBe("dark");
    mockSystemPrefersDark(false);
    expect(getEffectiveTheme()).toBe("light");
  });

  it("getEffectiveTheme prefers the stored override over the OS preference", () => {
    mockSystemPrefersDark(true);
    setTheme("light");
    expect(getEffectiveTheme()).toBe("light");
  });

  it("toggleTheme flips between light and dark and returns the new mode", () => {
    mockSystemPrefersDark(false);
    const first = toggleTheme();
    expect(first).toBe("dark");
    expect(getStoredTheme()).toBe("dark");

    const second = toggleTheme();
    expect(second).toBe("light");
    expect(getStoredTheme()).toBe("light");
  });

  it("applyTheme only touches the DOM attribute, not localStorage", () => {
    applyTheme("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem("theme")).toBeNull();
  });
});
