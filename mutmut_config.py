# print("*** IMPORTING CONFIG MUTMUT ***")


def init():
    # print("*** LOADING CONFIG MUTMUT ***")
    # only after initial test without mutation ?!
    pass


def pre_mutation(context):
    context.config.test_command = (
        "python local_test.py"
        f" {context.mutation_id_of_current_index.index+1} {context.mutation_id_of_current_index.filename}"
    )
