import { requestMaxContact } from "../../../integrations/max/bridge";
import { requestJson } from "../../../shared/base_http_client";

export interface StaffMembership {
  company_id: string;
  company_name: string;
  can_manage_staff: boolean;
  can_manage_residents: boolean;
  can_manage_issues: boolean;
}

export interface CurrentUser {
  id: string;
  kind: "unassigned" | "resident" | "employee" | "support" | "admin";
  full_name: string | null;
  full_name_confirmed: boolean;
  phone_number: string | null;
  phone_verified: boolean;
  staff_assignments: StaffMembership[];
}

export function getCurrentUser(): Promise<CurrentUser> {
  return requestJson<CurrentUser>("/auth/me");
}

export function updateProfileName(fullName: string): Promise<{ full_name: string; full_name_confirmed: boolean }> {
  return requestJson("/auth/update_profile_name", {
    method: "POST",
    body: JSON.stringify({ full_name: fullName }),
  });
}

export async function verifyPhoneWithMax(): Promise<CurrentUser> {
  const contact = await requestMaxContact();
  await requestJson("/auth/verify_contact", {
    method: "POST",
    body: JSON.stringify({ phone: contact.phone, auth_date: contact.authDate, signature: contact.hash }),
  });
  return getCurrentUser();
}
