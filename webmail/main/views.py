"""Webmail pages: landing, login/logout, inbox dashboard, delete and send."""

from functools import wraps

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .connection_manager import ConnectionManager
from .mailclient import MailError

SESSION_KEY = "cn_email"


def with_connection(view):
    """Pass the user's UserConnection to the view, or send them to the login page."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        email = request.session.get(SESSION_KEY)
        conn = ConnectionManager.get(email) if email else None
        if conn is None:
            request.session.flush()
            if request.headers.get("x-requested-with") == "fetch":
                return JsonResponse({"error": "Session expired, please log in again"}, status=401)
            messages.error(request, "Please log in to access the dashboard.")
            return redirect("login")
        return view(request, conn, *args, **kwargs)
    return wrapper


def home(request):
    return render(request, "main/index.html")


def login_page(request):
    if SESSION_KEY in request.session and ConnectionManager.get(request.session[SESSION_KEY]):
        return redirect("dashboard")

    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")
        if not email or not password:
            messages.error(request, "Email and password are required.")
            return redirect("login")
        try:
            ConnectionManager.open(email, password)
        except MailError as exc:
            messages.error(request, str(exc))
            return redirect("login")
        request.session.cycle_key()
        request.session[SESSION_KEY] = email
        return redirect("dashboard")

    return render(request, "main/signin.html")


def logout_page(request):
    email = request.session.get(SESSION_KEY)
    if email:
        ConnectionManager.close(email)
    request.session.flush()
    return redirect("home")


@with_connection
def dashboard(request, conn):
    try:
        conn.refresh_inbox()
        count, size = conn.stat()
    except MailError as exc:
        ConnectionManager.close(conn.email)
        request.session.flush()
        messages.error(request, f"{exc}. Please log in again.")
        return redirect("login")
    return render(request, "main/dashboard.html", {"cn": conn, "count": count, "size": size})


@require_POST
@with_connection
def delete_email(request, conn, id):
    if conn.delete_email(id):
        messages.success(request, "Email deleted")
    else:
        messages.error(request, "Error in deletion")
    return redirect("dashboard")


@require_POST
@with_connection
def send_email(request, conn):
    recipient = request.POST.get("recipient", "").strip()
    subject = request.POST.get("subject", "").strip()
    body = request.POST.get("message", "")
    if not all([recipient, subject, body]):
        return JsonResponse({"error": "Missing required fields"}, status=400)
    try:
        conn.send_email(subject, recipient, body)
    except MailError as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    return JsonResponse({"message": "Email sent successfully"})
