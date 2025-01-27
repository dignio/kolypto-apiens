import pytest
import sqlalchemy as sa
import sqlalchemy.orm

from apiens.error import exc


def test_converting_unexpected_errors():
    """ Test: converting_unexpected_errors() """
    from apiens.error.converting import converting_unexpected_errors

    # Test: convert ValueError
    with pytest.raises(exc.BaseApplicationError) as e:
        with converting_unexpected_errors():
            raise ValueError('INVALID')
    
    assert isinstance(e.value, exc.F_UNEXPECTED_ERROR)
    assert e.value.error == 'INVALID'


    # Test: convert a custom error
    class MyError(ValueError):
        def default_api_error(self):
            return exc.E_API_ARGUMENT.format(
                error='Wrong {name}: {value}',
                fixit='Check your {name}',
                name='param',
                value=str(self),
            )
        
    with pytest.raises(exc.BaseApplicationError) as e:
        with converting_unexpected_errors():
            raise MyError('INVALID')
    
    assert e.value.name == 'E_API_ARGUMENT'
    assert e.value.error == 'Wrong param: INVALID'


def test_converting_sqlalchemy_errors():
    """ Test: converting_sqlalchemy_errors() """
    from apiens.error.converting.sqlalchemy import converting_sa_errors

    def main():
        # Test: Not Found
        with pytest.raises(exc.E_NOT_FOUND):
            with Session() as ssn:
                with converting_sa_errors(Model=User):
                    ssn.query(User).filter_by(id=999).one()
        
        # Test: multiple results found
        with pytest.raises(exc.E_NOT_FOUND):
            with Session() as ssn:
                with converting_sa_errors(Model=User):
                    for _ in range(3):
                        ssn.add(User())
                    ssn.flush()

                    # Multiple results
                    ssn.query(User).one()
        
        # Test: integrity error
        with pytest.raises(exc.E_CONFLICT_DUPLICATE) as e:
            with Session() as ssn:
                with converting_sa_errors(Model=User):
                    ssn.add(User(login='user'))
                    ssn.flush()

                    # Add a conflicting PK
                    ssn.add(User(login='user'))
                    ssn.flush()

        assert e.value.name == 'E_CONFLICT_DUPLICATE'
        assert e.value.info == {
            'failed_columns': frozenset({'login'}),
            'object': 'User',
        }


    # Models
    from tests.lib import engine, Session, created_tables, declarative_base
    Base = declarative_base()

    class User(Base):
        id = sa.Column(sa.Integer, primary_key=True)
        login = sa.Column(sa.String, unique=True)
        
        __tablename__ = 'u'
        __table_args__ = (
            # Got to give this contraint a name.
            # Without a name, we won't be able to report it.
            sa.UniqueConstraint('login', name='u_login_key'),
        )

    # Go
    with created_tables(engine, Base.metadata):
        main() 
    