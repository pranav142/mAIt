from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base

# Base is a container for our schema. SQLAlchemy registers
# each class that inherits from Base represents a new table in our schema
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True)
    password = Column(String(100), unique=False, index=True)


# Add other tables as needed
