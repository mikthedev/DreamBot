from config import MAX_NICK_LENGTH


def display_base(member) -> str:
    """Prefer global display name, then username."""
    name = (member.global_name or member.name or "Member").strip()
    return name or "Member"


def build_nickname(discord_name: str, real_name: str) -> str:
    """
    Format: MikeGTC (Миша)
    Truncates the Discord name first if the full string exceeds Discord's limit.
    """
    real_name = real_name.strip()
    suffix = f" ({real_name})"
    if len(suffix) >= MAX_NICK_LENGTH:
        # Extreme edge case: keep as much of the real name as possible
        return real_name[:MAX_NICK_LENGTH]

    max_base = MAX_NICK_LENGTH - len(suffix)
    base = discord_name.strip()[:max_base].rstrip()
    return f"{base}{suffix}"


def is_guild_manager(member) -> bool:
    """Owner or anyone with Administrator / Manage Guild."""
    if member.guild.owner_id == member.id:
        return True
    perms = member.guild_permissions
    return bool(perms.administrator or perms.manage_guild)
