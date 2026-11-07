from sqlalchemy import create_engine, Column, String, Text, Integer, Float, UniqueConstraint
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

class Visitor(Base):
    __tablename__ = "visitors"
    id = Column(String(64), primary_key=True)
    created = Column(String(40), nullable=False)

class Case(Base):
    __tablename__ = "cases"
    id = Column(String(64), primary_key=True)
    owner = Column(String(64), nullable=False, index=True)
    data = Column(Text, nullable=False)
    revision = Column(Integer, nullable=False, default=0)

class Submission(Base):
    __tablename__ = "submissions"
    id = Column(String(64), primary_key=True)
    case_id = Column(String(64), nullable=False)
    digest = Column(String(64), nullable=False)
    paypal_dispute_id = Column(String(80), nullable=False, unique=True)
    state = Column(String(32), nullable=False)
    result = Column(Text, nullable=False, default="{}")
    __table_args__ = (UniqueConstraint("case_id", "digest"),)


class DraftJob(Base):
    __tablename__ = "draft_jobs"
    id = Column(String(64), primary_key=True)
    case_id = Column(String(64), nullable=False, index=True)
    input_digest = Column(String(64), nullable=False)
    state = Column(String(20), nullable=False, default="pending")
    expires = Column(Float, nullable=False)
    payload = Column(Text, nullable=False)
    lease_hash = Column(String(64), nullable=True)
    result = Column(Text, nullable=False, default="{}")
    error = Column(String(40), nullable=True)
    __table_args__ = (UniqueConstraint("case_id", "input_digest"),)

def database(url):
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
