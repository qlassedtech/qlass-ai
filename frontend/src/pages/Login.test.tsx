import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import Login from "./Login";
import { api, parentApi } from "../api";

// Login.tsx is the single unified entry point for all three login types
// (teacher/password, parent/OTP, student/OTP) — it decides which form to
// show next based on what api.checkPhone reports back. Mocking the API
// module lets us drive each branch without a real backend.
vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return {
    ...actual,
    api: { ...actual.api, checkPhone: vi.fn() },
    parentApi: { ...actual.parentApi, requestOtp: vi.fn() },
  };
});

function renderLogin() {
  return render(
    <MemoryRouter>
      <Login />
    </MemoryRouter>,
  );
}

describe("Login", () => {
  it("starts on the phone-number step", () => {
    renderLogin();
    expect(screen.getByPlaceholderText("91XXXXXXXXXX")).toBeInTheDocument();
  });

  it("routes a teacher phone number to the password step", async () => {
    vi.mocked(api.checkPhone).mockResolvedValue({ login_type: "password" });
    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByPlaceholderText("91XXXXXXXXXX"), "919123456780");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await waitFor(() => expect(screen.getByText("Enter your password")).toBeInTheDocument());
    expect(screen.queryByPlaceholderText("91XXXXXXXXXX")).not.toBeInTheDocument();
  });

  it("routes a linked parent's phone number to the parent OTP step, requesting the OTP", async () => {
    vi.mocked(api.checkPhone).mockResolvedValue({ login_type: "parent_otp" });
    vi.mocked(parentApi.requestOtp).mockResolvedValue({ sent: true });
    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByPlaceholderText("91XXXXXXXXXX"), "919777700099");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await waitFor(() => expect(screen.getByText("Enter the code")).toBeInTheDocument());
    expect(parentApi.requestOtp).toHaveBeenCalledWith("919777700099");
  });

  it("shows an error and stays on the phone step if checkPhone fails", async () => {
    vi.mocked(api.checkPhone).mockRejectedValue(new Error("Network error"));
    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByPlaceholderText("91XXXXXXXXXX"), "919000000000");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await waitFor(() => expect(screen.getByText("Network error")).toBeInTheDocument());
    expect(screen.getByPlaceholderText("91XXXXXXXXXX")).toBeInTheDocument();
  });
});
