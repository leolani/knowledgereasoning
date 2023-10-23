""" Filename:     RL.py
    Author(s):    Thomas Bellucci
    Description:  Implementation of Upper Confidence Bounds (UCB) used to
                  select Thoughts generated by the RLChatbot to verbalize.
    Date created: Nov. 11th, 2021
"""

import json
import os
import random

import matplotlib.pyplot as plt
import numpy as np
from rdflib import ConjunctiveGraph
from rdflib.extras.external_graph_libs import rdflib_to_networkx_multidigraph

from cltl.thoughts.api import ThoughtSelector


# from cltl.dialogue_evaluation.metrics.ontology_measures import get_avg_population
# from cltl.dialogue_evaluation.metrics.graph_measures import get_avg_degree, get_sparseness, get_shortest_path


class UCB(ThoughtSelector):
    def __init__(self, brain, reward="Total triples", savefile=None, c=2, tmax=1e10):
        """Initializes an instance of the Upper Confidence Bound
        (UCB) reinforcement learning algorithm.

        params
        float c:          controls level of exploration
        float tmax:       number of timesteps in which uncertainty of choices
                          are taken into account (exploitation when t > tmax)

        float t:          timestep
        float decay:      decay rate of exploration constant c
        float Q:          stores the estimate of the expected reward for each action
        float N:          stores the number of updates performed on each action

        returns: UCB object
        """
        super().__init__()

        # generic UBC parameters
        self._Q = dict()
        self._N = dict()
        self._t = 1
        self._c = c
        self._decay = c / tmax

        # Include rewards according to the state of the brain
        self._state_evaluator = BrainEvaluator(brain)
        self._log.debug(f"Brain state evaluator ready")
        self._reward = reward
        self._log.info(f"Reward: {self._reward}")

        # infrastructure to keep track of selections
        self._state_history = [self._state_evaluator.evaluate_brain_state(self._reward)]
        self._reward_history = [0]

        # Load learned policy
        self.load(savefile)
        self._log.debug(f"UCB RL Selector ready")

    @property
    def state_history(self):
        return self._state_history

    @property
    def reward_history(self):
        return self._reward_history

    # Utils

    def load(self, filename):
        """Reads utility values from file.

        params
        str filename: filename of file with utilities.

        returns: None
        """
        if filename is None:
            return

        if not os.path.isfile(filename):  # File exists?
            print("WARNING %s does not yet exist" % filename)
            return

        with open(filename, "r") as file:
            data = json.load(file)
            self._c = data["metadata"]["c"]
            self._t = data["metadata"]["t"]
            self._decay = data["metadata"]["decay"]

            for action, values in data["data"].items():
                self._Q[action] = values["value"]
                self._N[action] = values["count"]

    def save(self, filename):
        """Writes the value and uncertainty tables to a JSON file.

        params
        str filename: filename of the ouput file.

        returns: None
        """
        # Format metadata (c, t, decay) and value estimates as JSON.
        data = {
            "metadata": {"c": self._c, "t": self._t, "decay": self._decay},
            "data": dict(),
        }

        for action in self._Q.keys():
            if self._N[action] > 0:
                data["data"][action] = {
                    "value": self._Q[action],
                    "count": self._N[action],
                    "uncertainty": self._uncertainty(action),
                }
        # Write to file
        with open(filename, "w") as file:
            json.dump(data, file)

    # Learning

    def _uncertainty(self, action):
        """Computes the uncertainty associated with the current action
        as the upper confidence bound of the current average.

        params
        str action: an action

        returns:    UCB score of the action
        """
        return self._c * np.sqrt(np.log(self._t) / self._N[action])

    def select(self, actions):
        """Selects an action from the set of available actions that maximizes
        the average observed reward, taking into account uncertainty.

        params
        list actions: List of actions from which to select

        returns: action
        """
        # Safe processing
        actions = self._preprocess(actions)

        action_scores = []
        for action in actions:

            # Compute UCB score for each element of the action
            score = []
            for elem in action.split():

                # Add unseen elements to table
                if elem not in self._Q:
                    self._Q[elem] = 0
                    self._N[elem] = 0

                # Score action
                if self._N[elem] == 0:
                    score += [np.inf]  # ensures all actions are sampled at least once
                else:
                    score += [self._Q[elem] + self._uncertainty(elem)]

            # Convert element-scores into action score
            action_scores.append((action, np.mean(score)))

        # Greedy selection
        selected_action, _ = max(action_scores, key=lambda x: x[1])

        # Safe processing
        thought_type, thought_info = self._postprocess(actions, selected_action)

        return {thought_type: thought_info}

    def update_utility(self, action, reward):
        """Updates the action-value table (Q) by incrementally updating the
        reward estimate of the action elements with the observed reward.

        params
        str action:    selected action (with elements elem that are scored)
        float reward:  reward obtained after performing the action

        returns: None
        """
        # Update value estimates
        for elem in action.split():
            self._N[elem] += 1
            self._Q[elem] = self._Q[elem] + (reward - self._Q[elem]) / self._N[elem]

        # Update exploration constant
        self._t += 1
        self._c = max(self._c - self._decay, 0)

    def reward_thought(self):
        """Rewards the last thought phrased by the replier by updating its
        utility estimate with the relative improvement of the brain as
        a result of the user response (i.e. a reward).

        returns: None
        """
        brain_state = self._state_evaluator.evaluate_brain_state(self._reward)
        self._state_history.append(brain_state)
        self._log.info(f"Brain state: {brain_state}")

        # Reward last thought with R = S_brain(t) - S_brain(t-1)
        if self._last_thought and len(self._state_history) > 1:
            self._log.info(f"Calculate reward")
            new_state = self._state_history[-1]
            old_state = self._state_history[-2]
            reward = new_state / old_state

            self.update_utility(self._last_thought, reward)
            self.reward_history.append(reward)
            self._log.info(f"{reward} reward due to {self._last_thought}")

    # Plotting

    def plot(self, max_bars=16, filename=None):
        """Plots the value estimates for each action and their associated
        uncertainties in a bar plot.

        params
        float margin: Margin between bars

        returns: None
        """
        total_rewards = sum(self._N.values())  # empty value table?
        if total_rewards == 0:
            print("WARNING Cannot plot empty value table")
            return

        # Estimate value/uncertainty of actions
        a, q, u = [], [], []
        for action in sorted(list(self._Q.keys())):
            if self._N[action] > 0:
                a += [action]
                q += [self._Q[action]]
                u += [self._uncertainty(action)]

        # Reduce number of bars if > max_bars
        if len(a) > max_bars:
            idx = random.sample(range(len(a)), max_bars)  # TODO: Top not random
            a = [a[i] for i in idx]
            q = list(np.array(q)[idx])
            u = list(np.array(u)[idx])

        # Draw barplots for U and Q
        fig = plt.figure(figsize=(10, 5), tight_layout=True)
        plt.suptitle("$t=${}, $c=${}".format(self._t, round(self._c, 3)))

        plt.subplot(1, 2, 1)
        plt.ylabel("$Uncertainty$ $(U)$")
        plt.xlabel("$Actions$ $(a)$")
        plt.xticks(range(len(a)), a, rotation=45, ha="right")
        plt.bar(range(len(a)), u)

        plt.subplot(1, 2, 2)
        plt.ylabel("$Value$ $(Q)$")
        plt.xlabel("$Actions$ $(a)$")
        plt.xticks(range(len(a)), a, rotation=45, ha="right")
        plt.bar(range(len(a)), q)

        if filename:
            plt.savefig(filename / f"results.png", dpi=300)

        plt.show()


class BrainEvaluator(object):
    def __init__(self, brain):
        """ Create an object to evaluate the state of the brain according to different graph metrics.
        The graph can be evaluated by a single given metric, or a full set of pre established metrics
        """
        self._brain = brain

    def brain_as_graph(self):
        # Take brain from previous episodes
        graph = ConjunctiveGraph()
        graph.parse(data=self._brain._connection.export_repository(), format='trig')

        return graph

    def brain_as_netx(self):
        # Take brain from previous episodes
        netx = rdflib_to_networkx_multidigraph(self.brain_as_graph())

        return netx

    def evaluate_brain_state(self, metric):
        brain_state = None

        # if metric == 'Average degree':
        #     brain_state = get_avg_degree(self.brain_as_netx())
        # elif metric == 'Sparseness':
        #     brain_state = get_sparseness(self.brain_as_netx())
        # elif metric == 'Shortest path':
        #     brain_state = get_shortest_path(self.brain_as_netx())

        if metric == 'Total triples':
            brain_state = self._brain.count_triples()
        # elif metric == 'Average population':
        #     brain_state = get_avg_population(self.brain_as_graph())

        elif metric == 'Ratio claims to triples':
            brain_state = self._brain.count_statements() / self._brain.count_triples()
        elif metric == 'Ratio perspectives to claims':
            brain_state = self._brain.count_perspectives() / self._brain.count_statements()
        elif metric == 'Ratio conflicts to claims':
            brain_state = len(self._brain.get_all_negation_conflicts()) / self._brain.count_statements()

        return brain_state

    def calculate_brain_statistics(self, brain_response):
        # Grab the thoughts
        thoughts = brain_response['thoughts']

        # Gather basic stats
        stats = {
            'turn': brain_response['statement']['turn'],

            'cardinality conflicts': len(thoughts['_complement_conflict']) if thoughts['_complement_conflict'] else 0,
            'negation conflicts': len(thoughts['_negation_conflicts']) if thoughts['_negation_conflicts'] else 0,
            'subject gaps': len(thoughts['_subject_gaps']) if thoughts['_subject_gaps'] else 0,
            'object gaps': len(thoughts['_complement_gaps']) if thoughts['_complement_gaps'] else 0,
            'statement novelty': len(thoughts['_statement_novelty']) if thoughts['_statement_novelty'] else 0,
            'subject novelty': thoughts['_entity_novelty']['_subject'],
            'object novelty': thoughts['_entity_novelty']['_complement'],
            'overlaps subject-predicate': len(thoughts['_overlaps']['_subject'])
            if thoughts['_overlaps']['_subject'] else 0,
            'overlaps predicate-object': len(thoughts['_overlaps']['_complement'])
            if thoughts['_overlaps']['_complement'] else 0,
            'trust': thoughts['_trust'],

            'Total triples': self._brain.count_triples(),
            # 'Total classes': len(self._brain.get_classes()),
            # 'Total predicates': len(self._brain.get_predicates()),
            'Total claims': self._brain.count_statements(),
            'Total perspectives': self._brain.count_perspectives(),
            'Total conflicts': len(self._brain.get_all_negation_conflicts()),
            'Total sources': self._brain.count_friends(),
        }

        # Compute composite stats
        stats['Ratio claims to triples'] = stats['Total claims'] / stats['Total triples']
        stats['Ratio perspectives to triples'] = stats['Total perspectives'] / stats['Total triples']
        stats['Ratio conflicts to triples'] = stats['Total conflicts'] / stats['Total triples']
        stats['Ratio perspectives to claims'] = stats['Total perspectives'] / stats['Total claims']
        stats['Ratio conflicts to claims'] = stats['Total conflicts'] / stats['Total claims']

        return stats
