.PHONY: up seed backup verify check archive restore pitr test test-unit schedule schedule-once status down clean
PYTHON ?= python3
LAB = $(PYTHON) scripts/lab.py
up:
	$(LAB) up
seed:
	$(LAB) seed
backup:
	$(LAB) backup
verify:
	$(LAB) verify $(if $(BACKUP),--backup $(BACKUP))
check:
	$(LAB) check --service $(or $(SERVICE),db)
archive:
	$(LAB) archive
restore:
	$(LAB) restore $(if $(BACKUP),--backup $(BACKUP))
pitr:
	$(LAB) restore --target "$(TARGET)" $(if $(BACKUP),--backup $(BACKUP))
test-unit:
	$(PYTHON) -m unittest discover -s tests -v
test: test-unit
	$(LAB) test
schedule:
	$(LAB) schedule --interval $(or $(INTERVAL),3600)
schedule-once:
	$(LAB) schedule --once
status:
	$(LAB) status
down:
	$(LAB) down
clean:
	$(LAB) clean --confirm-lab-only
