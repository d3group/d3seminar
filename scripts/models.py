"""Database models shared by the API and private roster commands."""

import json

from sqlalchemy import Boolean, CheckConstraint, Column, Integer, LargeBinary, Table, Text, TypeDecorator, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, mapped_column


class Base(DeclarativeBase):
    pass


class JSONText(TypeDecorator):
    """Keep the existing TEXT schema; save changes by assigning a new dictionary."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return json.dumps(value)

    def process_result_value(self, value, dialect):
        return json.loads(value)


class Invitation(Base):
    __tablename__ = 'invitations'
    id = mapped_column(Text, primary_key=True, nullable=True)
    token_hash = mapped_column(Text, nullable=False, unique=True)
    data = mapped_column(JSONText, nullable=False)


class PollState(Base):
    __tablename__ = 'poll_state'
    __table_args__ = (CheckConstraint('id=1'),)
    id = mapped_column(Integer, primary_key=True, nullable=True)
    data = mapped_column(JSONText, nullable=False)


# The existing append-only history has no primary key; Core preserves that schema.
history = Table('history', Base.metadata,
                Column('saved_at', Text, nullable=False), Column('data', JSONText, nullable=False))


class Presentation(Base):
    __tablename__ = 'presentations'
    participant_id = mapped_column(Text, primary_key=True, nullable=True)
    data = mapped_column(JSONText, nullable=False)


class PresentationFile(Base):
    __tablename__ = 'presentation_files'
    __table_args__ = (UniqueConstraint('participant_id', 'kind'),)
    id = mapped_column(Text, primary_key=True, nullable=True)
    participant_id = mapped_column(Text, nullable=False)
    kind = mapped_column(Text, nullable=False)
    name = mapped_column(Text, nullable=False)
    mime = mapped_column(Text, nullable=False)
    content = mapped_column(LargeBinary, nullable=False, deferred=True)


class User(Base):
    __tablename__ = 'users'
    id = mapped_column(Text, primary_key=True)
    name = mapped_column(Text, nullable=False)
    email = mapped_column(Text(collation='NOCASE'), unique=True)
    role = mapped_column(Text, nullable=False)
    token = mapped_column(Text, nullable=False, unique=True)
    role_status = mapped_column(Text, nullable=False)
    is_organizer = mapped_column(Boolean, nullable=False, server_default='0')
    is_archived = mapped_column(Boolean, nullable=False, server_default='0')


class MailBatch(Base):
    __tablename__ = 'mail_batches'
    id = mapped_column(Text, primary_key=True)
    data = mapped_column(JSONText, nullable=False)
