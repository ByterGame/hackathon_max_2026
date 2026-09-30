import { useCallback, useEffect, useRef, useState } from "react";

import { getCurrentUser, verifyPhoneWithMax, type CurrentUser } from "./features/auth/integrations/client_api";
import { getAdminItem, type AdminEntity } from "./features/admin/integrations/client_api";
import { isDemoMode, issuesClient, type AppSnapshot } from "./features/issues/integrations/client_api";
import { getAccessRequest, type AccessRequestKind } from "./features/issues/integrations/access_actions_api";
import { demoResident, type Role } from "./features/issues/types";
import { listAppNotifications, markAllAppNotificationsRead, markAppNotificationRead, type AppNotification, type NotificationAudience } from "./features/notifications/integrations/client_api";
import { NotificationCenter, notificationDestination } from "./features/notifications/ui/NotificationCenter";
import { getLaunchIssueId } from "./integrations/max/bridge";
import { AdminHome } from "./pages/AdminHome";
import { AccessRequest } from "./pages/AccessRequest";
import { CompanyRegistration } from "./pages/CompanyRegistration";
import { AccessCasePanel, EmployeeAccess } from "./pages/EmployeeAccess";
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
import { ScreenHeader } from "./shared/common_ui/ScreenHeader";

type Screen = "home" | "issues" | "new" | "detail" | "access" | "registration" | "requests" | "employee-access" | "notifications" | "notification-request";

export function App() {
  const [role, setRole] = useState<Role | null>(null);
  const [screen, setScreen] = useState<Screen>("home");
  const [homeSection, setHomeSection] = useState<"houses" | "issues">("houses");
  const [residentReturnScreen, setResidentReturnScreen] = useState<"home" | "issues">("home");
  const [scrollToIssues, setScrollToIssues] = useState(false);
  const [selectedIssueId, setSelectedIssueId] = useState<string | null>(null);
  const [detailReturnScreen, setDetailReturnScreen] = useState<"home" | "issues" | "notifications">("home");
  const [selectedRequest, setSelectedRequest] = useState<ApplicantCase | null>(null);
  const [notificationRequest, setNotificationRequest] = useState<ApplicantCase | null>(null);
  const [supportNotificationRequest, setSupportNotificationRequest] = useState<{ kind: "company_registration" | "house_addition"; id: string } | null>(null);
  const [adminNotificationRecord, setAdminNotificationRecord] = useState<{ entity: AdminEntity; id: string } | null>(null);
  const [notificationsReturnScreen, setNotificationsReturnScreen] = useState<Screen>("home");
  const [notifications, setNotifications] = useState<AppNotification[]>([]);
  const [notificationsLoading, setNotificationsLoading] = useState(false);
  const [notificationsError, setNotificationsError] = useState("");
  const [notificationBusyId, setNotificationBusyId] = useState<string | null>(null);
  const [navigationNotice, setNavigationNotice] = useState("");
  const launchIssueId = useRef(getLaunchIssueId());
  const launchIssueHandled = useRef(false);
  const notificationLoadVersion = useRef(0);
  const [selectedHouseId, setSelectedHouseId] = useState("pushkina-5");
  const [selectedCompanyId, setSelectedCompanyId] = useState("");
  const [snapshot, setSnapshot] = useState<AppSnapshot | null>(null);
  const [dataError, setDataError] = useState("");
  const [account, setAccount] = useState<CurrentUser | null>(null);
  const [authLoading, setAuthLoading] = useState(!isDemoMode);
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState("");
  const notificationAudience: NotificationAudience | null = role ?? (account?.phone_verified && (account.kind === "support" || account.kind === "admin") ? account.kind : null);

  const applyAccount = useCallback((user: CurrentUser) => {
    notificationLoadVersion.current += 1;
    setAccount(user);
    setRole(user.phone_verified && user.kind !== "support" && user.kind !== "admin" ? user.kind === "employee" ? "employee" : "resident" : null);
    setSnapshot(null);
    setNotifications([]);
    setNotificationsError("");
    setSupportNotificationRequest(null);
    setAdminNotificationRecord(null);
    setNotificationRequest(null);
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

  const refreshNotifications = useCallback(async () => {
    if (!notificationAudience || (role && !snapshot)) return;
    const loadVersion = ++notificationLoadVersion.current;
    setNotificationsLoading(true);
    try {
      const items = await listAppNotifications(notificationAudience, snapshot);
      if (loadVersion === notificationLoadVersion.current) { setNotifications(items); setNotificationsError(""); }
    } catch {
      if (loadVersion === notificationLoadVersion.current) setNotificationsError("Не удалось загрузить уведомления. Проверьте соединение и повторите.");
    } finally {
      if (loadVersion === notificationLoadVersion.current) setNotificationsLoading(false);
    }
  }, [notificationAudience, role, snapshot]);

  useEffect(() => {
    if (!notificationAudience || (role && !snapshot)) return;
    void refreshNotifications();
    const timer = window.setInterval(() => { if (!document.hidden) void refreshNotifications(); }, 30000);
    const onVisible = () => { if (!document.hidden) void refreshNotifications(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [notificationAudience, role, snapshot, refreshNotifications]);

  useEffect(() => {
    if (!scrollToIssues || screen !== "home" || !snapshot) return;
    const frame = window.requestAnimationFrame(() => {
      document.getElementById(role === "employee" ? "employee-issues" : "issues-list")?.scrollIntoView({ behavior: "smooth" });
      setScrollToIssues(false);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [role, screen, scrollToIssues, snapshot]);

  async function confirmPhone() {
    setAuthBusy(true); setAuthError("");
    try { applyAccount(await verifyPhoneWithMax()); }
    catch (reason) { setAuthError(reason instanceof Error ? reason.message : "Не удалось подтвердить номер"); }
    finally { setAuthBusy(false); }
  }

  function selectDemoRole(nextRole: Role) {
    setSnapshot(null);
    setSelectedIssueId(null);
    setNotifications([]);
    setNotificationsError("");
    setScreen("home");
    setHomeSection("houses");
    setResidentReturnScreen("home");
    setRole(nextRole);
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function changeDemoRole() {
    setRole(null);
    setSnapshot(null);
    setSelectedIssueId(null);
    setNotifications([]);
    setNotificationsError("");
    setScreen("home");
    setHomeSection("houses");
    setResidentReturnScreen("home");
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function navigate(nextScreen: Screen) {
    setScreen(nextScreen);
    if (nextScreen === "home") setHomeSection("houses");
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function navigateFromResident(nextScreen: Screen) {
    if (screen === "home" || screen === "issues") setResidentReturnScreen(screen);
    navigate(nextScreen);
  }

  function returnToResident() {
    navigate(role === "resident" ? residentReturnScreen : "home");
  }

  function returnFromDetail() {
    navigate(detailReturnScreen);
  }

  function openNotifications() {
    setNotificationsReturnScreen(screen);
    setNotificationsError("");
    navigate("notifications");
    void refreshNotifications();
  }

  async function openDirectIssue(id: string, returnScreen: "home" | "issues" | "notifications") {
    if (!role || !snapshot) return;
    const issue = await issuesClient.getIssue(id, role);
    if (!snapshot.houses.some((house) => house.id === issue.houseId)) throw new Error("Эта проблема больше не доступна вашему аккаунту");
    setSnapshot((current) => current ? { ...current, issues: [issue, ...current.issues.filter((item) => item.id !== issue.id)] } : current);
    setSelectedIssueId(issue.id);
    setDetailReturnScreen(returnScreen);
    navigate("detail");
  }

  async function markNotification(item: AppNotification) {
    if (item.read_at) return;
    await markAppNotificationRead(item.id);
    notificationLoadVersion.current += 1;
    setNotificationsLoading(false);
    setNotifications((current) => current.map((entry) => entry.id === item.id ? { ...entry, read_at: new Date().toISOString() } : entry));
  }

  async function markAllNotifications() {
    if (!notificationAudience || notificationBusyId !== null) return;
    setNotificationBusyId("all");
    setNotificationsError("");
    try {
      await markAllAppNotificationsRead(notificationAudience, snapshot);
      await refreshNotifications();
    } catch {
      setNotificationsError("Не удалось отметить все уведомления прочитанными. Повторите позже.");
    } finally {
      setNotificationBusyId(null);
    }
  }

  async function openNotification(item: AppNotification) {
    if (!notificationAudience) return;
    setNotificationBusyId(item.id);
    setNotificationsError("");
    try {
      try { await markNotification(item); }
      catch { setNotificationsError("Не удалось отметить уведомление прочитанным. Откройте список позже и повторите."); }
      const destination = notificationDestination(item, notificationAudience);
      if (destination === "issue") {
        await openDirectIssue(item.subject_id, "notifications");
      } else if (destination === "request") {
        const requestKind: AccessRequestKind = item.subject_kind === "resident_request" ? "resident" : item.subject_kind === "company_registration_request" ? "company_registration" : "house_addition";
        if (!isDemoMode) await getAccessRequest(requestKind, item.subject_id);
        setNotificationRequest({ kind: requestKind, id: item.subject_id });
        navigate("notification-request");
      } else if (destination === "access") {
        navigate("access");
      } else if (destination === "employee-access") {
        const houseId = item.subject_kind === "resident_offer"
          ? snapshot?.residentOffers.find((entry) => entry.id === item.subject_id)?.houseId
          : item.subject_kind === "resident_grant"
            ? snapshot?.residentGrants.find((entry) => entry.id === item.subject_id)?.houseId
            : undefined;
        const companyId = item.subject_kind === "staff_assignment"
          ? snapshot?.staffAssignments.find((entry) => entry.id === item.subject_id)?.companyId
          : snapshot?.houses.find((house) => house.id === houseId)?.companyId;
        if (companyId) setSelectedCompanyId(companyId);
        navigate("employee-access");
      } else if (destination === "home") {
        navigate("home");
      } else if (destination === "support-request") {
        const kind = item.subject_kind === "company_registration_request" ? "company_registration" : "house_addition";
        await getAccessRequest(kind, item.subject_id);
        setSupportNotificationRequest({ kind, id: item.subject_id });
        navigate("home");
      } else if (destination === "admin-record") {
        const entityByKind: Record<string, AdminEntity> = {
          issue_card: "issues", resident_request: "access_requests", company_registration_request: "access_requests",
          house_addition_request: "access_requests", resident_offer: "offers", resident_grant: "resident_grants",
          staff_assignment: "staff",
        };
        const entity = entityByKind[item.subject_kind];
        if (!entity) throw new Error("Для этого уведомления нет страницы в кабинете администратора");
        await getAdminItem(entity, item.subject_id);
        setAdminNotificationRecord({ entity, id: item.subject_id });
        navigate("home");
      }
    } catch {
      setNotificationsError("Эта заявка или проблема сейчас недоступна. Обновите уведомления и попробуйте снова.");
    } finally {
      setNotificationBusyId(null);
    }
  }

  useEffect(() => {
    if (launchIssueHandled.current || !launchIssueId.current || !role || !snapshot) return;
    launchIssueHandled.current = true;
    void openDirectIssue(launchIssueId.current, "home")
      .catch(() => setNavigationNotice("Проблема по ссылке недоступна или была удалена. Откройте список проблем дома."));
  }, [role, snapshot]);

  function openHome() {
    setScrollToIssues(false);
    setHomeSection("houses");
    if (role === "resident") {
      if (screen === "home") window.scrollTo({ top: 0, behavior: "smooth" });
      else navigate("home");
      return;
    }
    if (screen === "home") window.scrollTo({ top: 0, behavior: "smooth" });
    else navigate("home");
  }

  function openIssues() {
    if (role === "resident") {
      if (screen === "issues") window.scrollTo({ top: 0, behavior: "smooth" });
      else navigate("issues");
      return;
    }
    if (screen === "home") document.getElementById("employee-issues")?.scrollIntoView({ behavior: "smooth" });
    else {
      setScrollToIssues(true);
      navigate("home");
    }
    setHomeSection("issues");
  }

  function openRequest(request: ApplicantCase | null) {
    setSelectedRequest(request);
    if (role === "resident") navigateFromResident("requests");
    else navigate("requests");
  }

  async function openSubmittedRegistration(id: string) {
    await refresh("resident");
    if (!role) setRole("resident");
    openRequest({ kind: "company_registration", id });
  }

  async function openIssue(id: string) {
    if (role === "resident" && (screen === "home" || screen === "issues")) setResidentReturnScreen(screen);
    setDetailReturnScreen(role === "resident" && screen === "issues" ? "issues" : "home");
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
  const notificationRequestHouse = snapshot?.houses.find((house) => house.id === snapshot.residentRequests.find((item) => item.id === notificationRequest?.id)?.houseId);
  const canManageNotifiedRequest = isDemoMode || Boolean(account?.staff_assignments.some((item) => item.company_id === notificationRequestHouse?.companyId && item.can_manage_residents));
  const unreadCount = notifications.filter((item) => !item.read_at).length;
  const showNav = role && snapshot && (screen === "home" || screen === "issues" || screen === "access" || screen === "requests" || screen === "employee-access" || screen === "notifications") && (role === "employee" || hasResidentAccess);

  return (
    <div className={`app-shell${showNav ? " app-shell--with-nav" : ""}`}>
      {isDemoMode && <div className="demo-banner"><span className="demo-banner__dot" /> Демонстрационный режим · данные хранятся только в этом браузере</div>}
      <div className="app-frame">
        {navigationNotice && <div className="panel error-state" role="alert">{navigationNotice}<button type="button" className="button button--soft" onClick={() => setNavigationNotice("")}>Понятно</button></div>}
        {!isDemoMode && authLoading && <div className="loading-state">Входим через MAX…</div>}
        {!isDemoMode && !authLoading && authError && !account && <div className="panel error-state" role="alert">{authError}<button type="button" className="button button--soft" onClick={() => void loadSession()}>Повторить</button></div>}
        {!isDemoMode && !authLoading && account && !account.phone_verified && <PhoneGate busy={authBusy} error={authError} onConfirm={() => void confirmPhone()} />}
        {!isDemoMode && !authLoading && account?.phone_verified && account.kind === "support" && screen === "home" && <SupportHome key={`${supportNotificationRequest?.kind ?? ""}:${supportNotificationRequest?.id ?? ""}`} actorId={account.id} initialRequest={supportNotificationRequest} onNotifications={openNotifications} unreadCount={unreadCount} />}
        {!isDemoMode && !authLoading && account?.phone_verified && account.kind === "admin" && screen === "home" && <AdminHome key={`${adminNotificationRecord?.entity ?? ""}:${adminNotificationRecord?.id ?? ""}`} currentUserId={account.id} initialRecord={adminNotificationRecord} onNotifications={openNotifications} unreadCount={unreadCount} onOwnAccountChanged={() => void loadSession()} />}
        {!isDemoMode && !authLoading && account?.phone_verified && account.kind === "unassigned" && <SupportInvitationNotice onAccepted={async () => applyAccount(await getCurrentUser())} />}

        {isDemoMode && !role && screen === "home" && <RoleSelection onContinue={selectDemoRole} onRegister={() => navigate("registration")} />}
        {(isDemoMode || account?.phone_verified) && screen === "registration" && <CompanyRegistration onBack={() => { if (role) void refresh(role); else if (isDemoMode) setRole("resident"); returnToResident(); }} onOpenRequest={(id) => void openSubmittedRegistration(id)} />}

        {role && !snapshot && !dataError && <div className="loading-state">Загружаем данные…</div>}
        {role && dataError && <div className="panel error-state" role="alert">{dataError}<button type="button" className="button button--soft" onClick={() => void refresh(role)}>Повторить</button></div>}

        {role === "resident" && snapshot && (screen === "home" || screen === "issues") && !hasResidentAccess && <OnboardingHome requests={snapshot.residentRequests} companyRequests={snapshot.companyRequests} houseRequests={snapshot.houseRequests} offers={snapshot.residentOffers} onAccess={() => navigateFromResident("access")} onRegister={() => navigateFromResident("registration")} onRequest={openRequest} onAllRequests={() => openRequest(null)} onNotifications={openNotifications} unreadCount={unreadCount} />}
        {role === "resident" && snapshot && (screen === "home" || screen === "issues") && hasResidentAccess && <ResidentHome view={screen} houses={snapshot.houses} issues={snapshot.issues} grants={snapshot.residentGrants} requests={snapshot.residentRequests} companyRequests={snapshot.companyRequests} houseRequests={snapshot.houseRequests} currentUserId={isDemoMode ? demoResident.id : account?.id ?? ""} selectedHouseId={activeHouseId ?? ""} onSelectHouse={setSelectedHouseId} onHome={openHome} onIssues={openIssues} onNew={() => navigateFromResident("new")} onIssue={(id) => void openIssue(id)} onAccess={() => navigateFromResident("access")} onRegister={() => navigateFromResident("registration")} onRequest={openRequest} onAllRequests={() => openRequest(null)} onNotifications={openNotifications} unreadCount={unreadCount} onChangeRole={isDemoMode ? changeDemoRole : undefined} />}
        {role === "resident" && snapshot && screen === "requests" && <MyRequests residentRequests={snapshot.residentRequests} companyRequests={snapshot.companyRequests} houseRequests={snapshot.houseRequests} initialCase={selectedRequest} onBack={returnToResident} onChanged={() => refresh("resident")} />}
        {role === "employee" && snapshot && screen === "home" && <EmployeeHome houses={snapshot.houses} issues={snapshot.issues} onIssue={(id) => void openIssue(id)} onAccess={() => navigate("employee-access")} onNotifications={openNotifications} unreadCount={unreadCount} onChangeRole={isDemoMode ? changeDemoRole : undefined} />}
        {notificationAudience && screen === "notifications" && <NotificationCenter role={notificationAudience} snapshot={snapshot} items={notifications} loading={notificationsLoading} error={notificationsError} busyId={notificationBusyId} onBack={() => navigate(notificationsReturnScreen)} onRefresh={() => void refreshNotifications()} onMarkAll={() => void markAllNotifications()} onOpen={(item) => void openNotification(item)} onMarkRead={(item) => void markNotification(item).catch(() => setNotificationsError("Не удалось отметить уведомление прочитанным. Повторите позже."))} />}
        {role && snapshot && screen === "notification-request" && notificationRequest && <div className="page page--form"><ScreenHeader title="Заявка" subtitle="Статус и обсуждение" onBack={() => navigate("notifications")} /><AccessCasePanel kind={notificationRequest.kind} id={notificationRequest.id} perspective={role === "employee" && notificationRequest.kind === "resident" ? "staff" : "applicant"} canManage={role === "employee" && notificationRequest.kind === "resident" && canManageNotifiedRequest} onChanged={() => refresh(role)} /></div>}
        {role === "resident" && snapshot && screen === "new" && residentHouse && <IssueComposer house={residentHouse} grants={activeGrants.filter((item) => item.houseId === residentHouse.id)} categories={snapshot.categories} onBack={returnToResident} onOpenIssue={(id) => void openIssue(id)} />}
        {role === "resident" && snapshot && screen === "access" && <AccessRequest houses={snapshot.houses} requests={snapshot.residentRequests} offers={snapshot.residentOffers} initialName={isDemoMode ? demoResident.name : account?.full_name_confirmed ? account.full_name ?? undefined : undefined} nameConfirmed={isDemoMode || Boolean(account?.full_name_confirmed)} onBack={returnToResident} onChanged={() => refresh("resident")} onAccountChanged={refreshAccount} onOpenRequest={(id) => openRequest({ kind: "resident", id })} />}
        {role === "employee" && snapshot && screen === "employee-access" && <EmployeeAccess houses={companyHouses} requests={snapshot.residentRequests.filter((item) => companyHouseIds.has(item.houseId))} grants={snapshot.residentGrants.filter((item) => companyHouseIds.has(item.houseId))} offers={snapshot.residentOffers.filter((item) => companyHouseIds.has(item.houseId))} staff={snapshot.staffAssignments.filter((item) => item.companyId === activeCompanyId)} houseRequests={snapshot.houseRequests.filter((item) => item.companyId === activeCompanyId)} permissions={staffPermissions} companyId={activeCompanyId} companies={staffMemberships.map((item) => ({ id: item.company_id, name: item.company_name }))} onCompanyChange={setSelectedCompanyId} onBack={() => navigate("home")} onChanged={() => refresh("employee")} />}
        {role && snapshot && screen === "detail" && selectedIssue && selectedHouse && <IssueDetail issue={selectedIssue} house={selectedHouse} role={role} currentUserId={isDemoMode ? demoResident.id : account?.id ?? ""} canManageIssues={role === "resident" || issueStaffPermission} categories={snapshot.categories} relatedIssues={snapshot.issues.filter((item) => item.houseId === selectedIssue.houseId && item.id !== selectedIssue.id && item.status !== "closed")} onBack={returnFromDetail} onChanged={() => refresh(role)} onMerged={async (id) => { await refresh(role); setSelectedIssueId(id); }} />}
        {role && snapshot && screen === "detail" && (!selectedIssue || !selectedHouse) && <div className="empty-state panel"><strong>Карточка не найдена</strong><button type="button" className="button button--soft" onClick={returnFromDetail}>Вернуться к списку</button></div>}
      </div>
      {showNav && <nav className={`bottom-nav${role === "resident" ? " bottom-nav--resident" : ""}`} aria-label="Основная навигация">
        <button type="button" className={role === "resident" ? screen === "home" ? "bottom-nav__active" : "" : screen === "home" && homeSection === "houses" ? "bottom-nav__active" : ""} aria-current={role === "resident" ? screen === "home" ? "page" : undefined : screen === "home" && homeSection === "houses" ? "page" : undefined} onClick={openHome}><Icon name={role === "resident" ? "home" : "building"} size={24} /><span>{role === "resident" ? "Мои дома" : "Панель УК"}</span></button>
        <button type="button" className={role === "resident" ? screen === "issues" ? "bottom-nav__active" : "" : screen === "home" && homeSection === "issues" ? "bottom-nav__active" : ""} aria-current={role === "resident" ? screen === "issues" ? "page" : undefined : screen === "home" && homeSection === "issues" ? "page" : undefined} onClick={openIssues}><Icon name="list" size={24} /><span>Проблемы</span></button>
        {role === "resident" ? <><button type="button" onClick={() => navigateFromResident("new")}><Icon name="plus" size={24} /><span>Создать</span></button><button type="button" className={screen === "access" || screen === "requests" ? "bottom-nav__active" : ""} aria-current={screen === "access" || screen === "requests" ? "page" : undefined} onClick={() => navigateFromResident("access")}><Icon name="key" size={24} /><span>Доступ</span></button></> : <><button type="button" className={screen === "employee-access" ? "bottom-nav__active" : ""} aria-current={screen === "employee-access" ? "page" : undefined} onClick={() => navigate("employee-access")}><Icon name="key" size={24} /><span>Доступы</span></button>{isDemoMode && <button type="button" onClick={changeDemoRole}><Icon name="user" size={24} /><span>Сменить роль</span></button>}</>}
      </nav>}
    </div>
  );
}
