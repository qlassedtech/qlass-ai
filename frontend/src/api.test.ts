import { describe, expect, it } from "vitest";
import {
  isParentAuthenticated,
  isStudentAuthenticated,
  setParentToken,
  setStudentToken,
  setToken,
} from "./api";

describe("auth token helpers", () => {
  it("teacher token: setToken stores and clears the token key", () => {
    setToken("abc123");
    expect(localStorage.getItem("token")).toBe("abc123");
    setToken(null);
    expect(localStorage.getItem("token")).toBeNull();
  });

  it("student token: isStudentAuthenticated reflects presence of student_token", () => {
    expect(isStudentAuthenticated()).toBe(false);
    setStudentToken("student-abc");
    expect(isStudentAuthenticated()).toBe(true);
    setStudentToken(null);
    expect(isStudentAuthenticated()).toBe(false);
  });

  it("parent token: isParentAuthenticated reflects presence of parent_token", () => {
    expect(isParentAuthenticated()).toBe(false);
    setParentToken("parent-abc");
    expect(isParentAuthenticated()).toBe(true);
    setParentToken(null);
    expect(isParentAuthenticated()).toBe(false);
  });

  it("the three auth sessions are independent — setting one doesn't affect the others", () => {
    setToken("teacher-token");
    setStudentToken("student-token");
    setParentToken("parent-token");

    expect(localStorage.getItem("token")).toBe("teacher-token");
    expect(isStudentAuthenticated()).toBe(true);
    expect(isParentAuthenticated()).toBe(true);

    setStudentToken(null);
    expect(localStorage.getItem("token")).toBe("teacher-token"); // unaffected
    expect(isStudentAuthenticated()).toBe(false);
    expect(isParentAuthenticated()).toBe(true); // unaffected
  });
});
