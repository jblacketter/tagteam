# Code Review Checklist

Use this checklist when reviewing implemented code.

## Correctness
- [ ] Implementation matches the phase plan
- [ ] All success criteria are met
- [ ] No obvious bugs or logic errors
- [ ] Edge cases are handled appropriately
- [ ] Error handling is appropriate

## Code Quality
- [ ] Code is readable and clear
- [ ] No unnecessary complexity
- [ ] Functions/methods are appropriately sized
- [ ] Variable names are descriptive
- [ ] No code duplication that should be refactored

## Language Standards
- [ ] Follows language style guidelines
- [ ] Imports/dependencies are organized
- [ ] Type hints/annotations used where helpful
- [ ] Documentation for complex functions

## Framework Specifics
- [ ] Routes/endpoints are logically organized
- [ ] Templates/views are properly structured
- [ ] Database queries are efficient
- [ ] No N+1 query problems
- [ ] Proper use of framework patterns

## Security
- [ ] No injection vulnerabilities (SQL, command, etc.)
- [ ] No XSS in templates/output
- [ ] Input validation present
- [ ] No hardcoded secrets or credentials
- [ ] Authentication/authorization where needed

## Testing
- [ ] Key functionality has tests
- [ ] Tests pass
- [ ] Application runs without errors
- [ ] Manual testing confirms behavior
