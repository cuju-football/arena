"""A stage type that plays one round robin and then a final between the first two of the table.

The tiebreaker `clean_sheets` orders level entrants by the matches in which they conceded no goal.
"""


class LeagueFinal:
    def options(self):
        return {'final_name': {'type': 'string', 'default': 'Final'}}

    def schedule(self, plan):
        seeds = plan.seeds
        pairs = [(a, b) for place, a in enumerate(seeds) for b in seeds[place + 1 :]]
        fixtures = [
            plan.fixture(f'g{number}', f'Match {number}', home, away)
            for number, (home, away) in enumerate(pairs, start=1)
        ]
        rounds = [plan.round('League', fixtures)]
        # The table is read before the final, whose matches count in the stage's matches too.
        plan.state['table'] = plan.table(seeds)
        plan.state['final'] = None
        if all(fixture['state'] == 'done' for fixture in fixtures):
            first, second = (row['entrant'] for row in plan.state['table'][:2])
            name = plan.settings['final_name']
            final = plan.tie('final', name, first, second)
            rounds.append(plan.round(name, [final]))
            plan.state['final'] = final
        return {'rounds': rounds}

    def complete(self, plan, scheduled):
        final = plan.state['final']
        return final is not None and final['state'] == 'done'

    def planned(self, settings, count):
        # An optional member, every pair once and the final, so the page counts from the start.
        return count * (count - 1) // 2 + 1

    def standings(self, plan, scheduled):
        table = plan.state['table']
        ranking = None
        if self.complete(plan, scheduled):
            final = plan.state['final']
            ranking = [final['winner'], final['loser']]
            ranking += [row['entrant'] for row in table if row['entrant'] not in ranking]
        return {'tables': [plan.standing('League', table, 2)], 'ranking': ranking}


class CleanSheets:
    def key(self, rows, options):
        found = dict.fromkeys(rows, 0)
        for match in options['entries']:
            home, away = match['score']
            if away == 0:
                found[match['home']] += 1
            if home == 0:
                found[match['away']] += 1
        return found


def register(registry):
    registry.provide('stage_type/1', 'league_final', LeagueFinal())
    registry.provide('tiebreaker/1', 'clean_sheets', CleanSheets())
