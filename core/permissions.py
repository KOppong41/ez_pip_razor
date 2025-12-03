from rest_framework.permissions import BasePermission, SAFE_METHODS

def _in_group(user, name): 
    return user.is_authenticated and user.groups.filter(name=name).exists()

class IsAdmin(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_superuser or _in_group(request.user, "Admin")

class IsOps(BasePermission):
    def has_permission(self, request, view):
        return _in_group(request.user, "Ops") or _in_group(request.user, "Admin") or request.user.is_superuser

class ReadOnly(BasePermission):
    def has_permission(self, request, view):
        return request.method in SAFE_METHODS

class IsOpsOrReadOnly(BasePermission):
    """Ops/Admin can write; everyone authenticated can read."""
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return request.user.is_authenticated
        return _in_group(request.user, "Ops") or _in_group(request.user, "Admin") or request.user.is_superuser

class IsAdminOnlyWrite_ReadForAllAuth(BasePermission):
    """Only Admin can write; authenticated users can read."""
    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return request.user.is_authenticated
        return _in_group(request.user, "Admin") or request.user.is_superuser
