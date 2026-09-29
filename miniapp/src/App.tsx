import { useCallback, useEffect, useState } from "react";

import { getCurrentUser, verifyPhoneWithMax, type CurrentUser } from "./features/auth/integrations/client_api";
import { isDemoMode, issuesClient, type AppSnapshot } from "./features/issues/integrations/client_api";
import { demoResident, type Role } from "./features/issues/types";
import { AdminHome } from "./pages/AdminHome";
import { AccessRequest } from "./pages/AccessRequest";
import { CompanyRegistration } from "./pages/CompanyRegistration";
import { EmployeeAccess } from "./pages/EmployeeAccess";
import { EmployeeHome } from "./pages/EmployeeHome";
import { IssueComposer } from "./pages/IssueComposer";
import { IssueDetail } from "./pages/IssueDetail";
import { MyRequests, type ApplicantCase } from "./pages/MyRequests";
import { OnboardingHome } from "./pages/OnboardingHome";
import { PhoneGate } from "./pages/PhoneGate";
import { ResidentHome } from "./pages/ResidentHome";
import { RoleSelection } from "./pages/RoleSelection";
import { SupportInvitationNotice } from "./pages/SupportInvitation";
import { SupportHome } from "./pages/SupportHome";
import { Icon } from "./shared/common_ui/Icon";

type Screen = "home" | "new" | "detail" | "access" | "registration" | "requests" | "employee-access";

export function App() {
  const [role, setRole] = useState<Role | null>(null);
  const [screen, setScreen] = useState<Screen>("home");
  const [selectedIssueId, setSelectedIssueId] = useState<string | null>(null);
  const [selectedRequest, setSelectedRequest] = useState<ApplicantCase | null>(null);
  const [selectedHouseId, setSelectedHouseId] = useState("pushkina-5");
  const [selectedCompanyId, setSelectedCompanyId] = useState("");
  const [snapshot, setSnapshot] = useState<AppSnapshot | null>(null);
  const [dataError, setDataError] = useState("");
  const [account, setAccount] = useState<CurrentUser | null>(null);
  const [authLoading, setAuthLoading] = useState(!isDemoMode);
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState("");

  const applyAccount = useCallback((user: CurrentUser) => {
    setAccount(user);
    setRole(user.phone_verified && user.kind !== "support" && user.kind !== "admin" ? user.kind === "employee" ? "employee" : "resident" : null);
    setScreen("home");
    setAuthError("");
  }, []);

  const loadSession = useCallback(async () => {
    setAuthLoading(true);
    try { applyAccount(await getCurrentUser()); }
    catch (reason) { setAccount(null); setRole(null); setAuthError(reason instanceof Error ? reason.message : "Не удалось войти через MAX"); }
    finally { setAuthLoading(false); }
  }, [applyAccount]);

  const refreshAccount = useCallback(async () => {
    if (!isDemoMode) setAccount(await getCurrentUser());
  }, []);

  useEffect(() => { if (!isDemoMode) void loadSession(); }, [loadSession]);

  const refresh = useCallback(async (currentRole: Role) => {
    try { setSnapshot(await issuesClient.getSnapshot(currentRole)); setDataError(""); }
    catch (reason) { setDataError(reason instanceof Error ? reason.message : "Не удалось загрузить данные"); }
  }, []);

  useEffect(() => { if (role) void refresh(role); }, [role, refresh]);

  async function confirmPhone() {
    setAuthBusy(true); setAuthError("");
    try { applyAccount(await verifyPhoneWithMax()); }
    catch (reason) { setAuthError(reason instanceof Error ? reason.message : "Не удалось подтвердить номер"); }
    finally { setAuthBusy(false); }
  }

  function selectDemoRole(nextRole: Role) {
    setSnapshot(null);
    setSelectedIssueId(null);
    setScreen("home");
    setRole(nextRole);
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function changeDemoRole() {
    setRole(null);
    setSnapshot(null);
    setSelectedIssueId(null);
    setScreen("home");
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function navigate(nextScreen: Screen) {
    setScreen(nextScreen);
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function openRequest(request: ApplicantCase | null) {
    setSelectedRequest(request);
    navigate("requests");
  }

  async function openSubmittedRegistration(id: string) {
    await refresh("resident");
    if (!role) setRole("resident");
    openRequest({ kind: "company_registration", id });
  }

  async function openIssue(id: string) {
    if (role) await refresh(role);
    setSelectedIssueId(id);
    navigate("detail");
  }

  const selectedIssue = snapshot?.issues.find((item) => item.id === selectedIssueId);
  const selectedHouse = snapshot?.houses.find((item) => item.id === selectedIssue?.houseId);
  const activeGrants = snapshot?.residentGrants.filter((item) => item.status !== "revoked" && item.status !== "expired" && (!item.validUntil || new Date(item.validUntil).getTime() > Date.now())) ?? [];
  const activeHouseId = activeGrants.some((item) => item.houseId === selectedHouseId) ? selectedHouseId : activeGrants[0]?.houseId;
  const residentHouse = snapshot?.houses.find((item) => item.id === activeHouseId);
  const hasResidentAccess = activeGrants.length > 0;
  const staffMemberships = isDemoMode ? [{ company_id: "demo-company", company_name: "УК «Дом-Сервис»", can_manage_staff: true, can_manage_residents: true, can_manage_issues: true }] : account?.staff_assignments ?? [];
  const staffMembership = staffMemberships.find((item) => item.company_id === selectedCompanyId) ?? staffMemberships[0];
  const activeCompanyId = staffMembership?.company_id ?? "";
  const companyHouses = snapshot?.houses.filter((item) => item.companyId === activeCompanyId) ?? [];
  const companyHouseIds = new Set(companyHouses.map((item) => item.id));
  const staffPermissions = isDemoMode ? { manageStaff: true, manageResidents: true, manageIssues: true } : { manageStaff: Boolean(staffMembership?.can_manage_staff), manageResidents: Boolean(staffMembership?.can_manage_residents), manageIssues: Boolean(staffMembership?.can_manage_issues) };
  const issueStaffPermission = isDemoMode || Boolean(account?.staff_assignments.find((item) => item.company_id === selectedHouse?.companyId)?.can_manage_issues);
  const showNav = role && snapshot && screen === "home" && (role === "employee" || hasResidentAccess);

  return (
    <div className="app-shell">
      {isDemoMode && <div className="demo-banner"><span className="demo-banner__dot" /> Демонстрационный режим · данные хранятся только в этом браузере</div>}
      <div className="app-frame">
        {!isDemoMode && authLoading && <div className="loading-state">Входим через MAX…</div>}
        {!isDemoMode && !authLoading && authError && !account && <div className="panel error-state" role="alert">{authError}<button type="button" className="button button--soft" onClick={() => void loadSession()}>Повторить</button></div>}
        {!isDemoMode && !authLoading && account && !account.phone_verified && <PhoneGate busy={authBusy} error={authError} onConfirm={() => void confirmPhone()} />}
        {!isDemoMode && !authLoading && account?.phone_verified && account.kind === "support" && <SupportHome actorId={account.id} />}
        {!isDemoMode && !authLoading && account?.phone_verified && account.kind === "admin" && <AdminHome currentUserId={account.id} onOwnAccountChanged={() => void loadSession()} />}
        {!isDemoMode && !authLoading && account?.phone_verified && account.kind === "unassigned" && <SupportInvitationNotice onAccepted={async () => applyAccount(await getCurrentUser())} />}

        {isDemoMode && !role && screen === "home" && <RoleSelection onContinue={selectDemoRole} onRegister={() => navigate("registration")} />}
        {(isDemoMode || account?.phone_verified) && screen === "registration" && <CompanyRegistration onBack={() => { if (role) void refresh(role); else if (isDemoMode) setRole("resident"); navigate("home"); }} onOpenRequest={(id) => void openSubmittedRegistration(id)} />}

        {role && !snapshot && !dataError && <div className="loading-state">Загружаем данные…</div>}
        {role && dataError && <div className="panel error-state" role="alert">{dataError}<button type="button" className="button button--soft" onClick={() => void refresh(role)}>Повторить</button></div>}

        {role === "resident" && snapshot && screen === "home" && !hasResidentAccess && <OnboardingHome requests={snapshot.residentRequests} companyRequests={snapshot.companyRequests} houseRequests={snapshot.houseRequests} offers={snapshot.residentOffers} onAccess={() => navigate("access")} onRegister={() => navigate("registration")} onRequest={openRequest} onAllRequests={() => openRequest(null)} />}
        {role === "resident" && snapshot && screen === "home" && hasResidentAccess && <ResidentHome houses={snapshot.houses} issues={snapshot.issues} grants={snapshot.residentGrants} requests={snapshot.residentRequests} companyRequests={snapshot.companyRequests} houseRequests={snapshot.houseRequests} currentUserId={isDemoMode ? demoResident.id : account?.id ?? ""} selectedHouseId={activeHouseId ?? ""} onSelectHouse={setSelectedHouseId} onNew={() => navigate("new")} onIssue={(id) => void openIssue(id)} onAccess={() => navigate("access")} onRegister={() => navigate("registration")} onRequest={openRequest} onAllRequests={() => openRequest(null)} onChangeRole={isDemoMode ? changeDemoRole : undefined} />}
        {role === "resident" && snapshot && screen === "requests" && <MyRequests residentRequests={snapshot.residentRequests} companyRequests={snapshot.companyRequests} houseRequests={snapshot.houseRequests} initialCase={selectedRequest} onBack={() => navigate("home")} onChanged={() => refresh("resident")} />}
        {role === "employee" && snapshot && screen === "home" && <EmployeeHome houses={snapshot.houses} issues={snapshot.issues} onIssue={(id) => void openIssue(id)} onAccess={() => navigate("employee-access")} onChangeRole={isDemoMode ? changeDemoRole : undefined} />}
        {role === "resident" && snapshot && screen === "new" && residentHouse && <IssueComposer house={residentHouse} categories={snapshot.categories} onBack={() => navigate("home")} onOpenIssue={(id) => void openIssue(id)} />}
        {role === "resident" && snapshot && screen === "access" && <AccessRequest houses={snapshot.houses} requests={snapshot.residentRequests} offers={snapshot.residentOffers} initialName={isDemoMode ? demoResident.name : account?.full_name_confirmed ? account.full_name ?? undefined : undefined} nameConfirmed={isDemoMode || Boolean(account?.full_name_confirmed)} onBack={() => navigate("home")} onChanged={() => refresh("resident")} onAccountChanged={refreshAccount} onOpenRequest={(id) => openRequest({ kind: "resident", id })} />}
        {role === "employee" && snapshot && screen === "employee-access" && <EmployeeAccess houses={companyHouses} requests={snapshot.residentRequests.filter((item) => companyHouseIds.has(item.houseId))} grants={snapshot.residentGrants.filter((item) => companyHouseIds.has(item.houseId))} offers={snapshot.residentOffers.filter((item) => companyHouseIds.has(item.houseId))} staff={snapshot.staffAssignments.filter((item) => item.companyId === activeCompanyId)} houseRequests={snapshot.houseRequests.filter((item) => item.companyId === activeCompanyId)} permissions={staffPermissions} companyId={activeCompanyId} companies={staffMemberships.map((item) => ({ id: item.company_id, name: item.company_name }))} onCompanyChange={setSelectedCompanyId} onBack={() => navigate("home")} onChanged={() => refresh("employee")} />}
        {role && snapshot && screen === "detail" && selectedIssue && selectedHouse && <IssueDetail issue={selectedIssue} house={selectedHouse} role={role} currentUserId={isDemoMode ? demoResident.id : account?.id ?? ""} canManageIssues={role === "resident" || issueStaffPermission} categories={snapshot.categories} relatedIssues={snapshot.issues.filter((item) => item.houseId === selectedIssue.houseId && item.id !== selectedIssue.id && item.status !== "closed")} onBack={() => navigate("home")} onChanged={() => refresh(role)} onMerged={async (id) => { await refresh(role); setSelectedIssueId(id); }} />}
        {role && snapshot && screen === "detail" && (!selectedIssue || !selectedHouse) && <div className="empty-state panel"><strong>Карточка не найдена</strong><button type="button" className="button button--soft" onClick={() => navigate("home")}>Вернуться к списку</button></div>}
      </div>
      {showNav && <nav className="bottom-nav" aria-label="Основная навигация">
        <button type="button" className="bottom-nav__active" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}><Icon name={role === "resident" ? "home" : "building"} /><span>{role === "resident" ? "Мои дома" : "Панель УК"}</span></button>
        <button type="button" onClick={() => document.getElementById(role === "resident" ? "issues-list" : "employee-issues")?.scrollIntoView({ behavior: "smooth" })}><Icon name="list" /><span>Проблемы</span></button>
        {role === "resident" ? <><button type="button" onClick={() => navigate("new")}><Icon name="plus" /><span>Создать</span></button><button type="button" onClick={() => navigate("access")}><Icon name="key" /><span>Доступ</span></button></> : <><button type="button" onClick={() => navigate("employee-access")}><Icon name="key" /><span>Доступы</span></button>{isDemoMode && <button type="button" onClick={changeDemoRole}><Icon name="user" /><span>Сменить роль</span></button>}</>}
      </nav>}
    </div>
  );
}
